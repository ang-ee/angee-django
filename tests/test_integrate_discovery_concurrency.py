"""PostgreSQL connection discovery races through independent row-lock owners."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import actor_context, system_context

from angee.integrate.discovery import ConnectionDiscovery
from tests.conftest import Credential, Integration, create_platform_admin, create_user
from tests.integrate_fixtures import ConnectionBridge
from tests.lock_waits import submit, wait_for_lock
from tests.test_integrate_discovery import bridge as bridge
from tests.test_integrate_discovery import connection_settings as connection_settings
from tests.test_integrate_discovery import sent_tasks as sent_tasks

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_attach_commits_before_blocked_finish_and_rejects_all_stale_side_effects(bridge):
    old_credential, generation = bridge.credential_id, bridge.discovery_generation
    with system_context(reason="test replacement grant"):
        replacement = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="new connection", material={"api_key": "fake"},
        )

    def finish():
        with system_context(reason="test independent discovery finish"):
            row = ConnectionBridge.objects.get(pk=bridge.pk)
            return row.finish_discovery(
                credential_pk=old_credential, generation=generation,
                discovery=ConnectionDiscovery(data={"display_name": "Stale resource"}),
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test attachment wins"), transaction.atomic():
            ConnectionBridge.objects.lock_if_supported().get(pk=bridge.pk)
            bridge.attach_credential(replacement)
            contender, pid = submit(pool, finish)
            wait_for_lock(pid, contender)
        assert contender.result(timeout=10) is False
    bridge.refresh_from_db()
    assert bridge.display_name == ""
    assert bridge.credential_id == replacement.pk
    assert bridge.discovery_generation == generation + 1 and bridge.discovery_pending


def test_finish_commits_before_blocked_attach_and_new_attachment_clears_completion(bridge):
    credential_pk, generation = bridge.credential_id, bridge.discovery_generation

    def attach():
        with system_context(reason="test independent connection attach"):
            row = ConnectionBridge.objects.get(pk=bridge.pk)
            row.attach_credential(row.credential)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test discovery wins"), transaction.atomic():
            ConnectionBridge.objects.lock_if_supported().get(pk=bridge.pk)
            assert bridge.finish_discovery(
                credential_pk=credential_pk, generation=generation,
                discovery=ConnectionDiscovery(data={"display_name": "First resource"}),
            )
            contender, pid = submit(pool, attach)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    bridge.refresh_from_db()
    assert bridge.display_name == "First resource"
    assert bridge.discovery_generation == generation + 1 and bridge.discovery_pending
    assert bridge.discovery_completed_at is None and bridge.next_sync_at is None


@pytest.mark.parametrize("verb", ["resume", "retry_discovery", "transfer_ownership"])
def test_parent_verb_waits_for_the_concrete_row_without_holding_the_parent(bridge, verb):
    with system_context(reason="test parent verb lock order"):
        if verb == "resume":
            bridge.pause()
        elif verb == "retry_discovery":
            bridge.config = {"discovery": "refusal"}
            bridge.save(update_fields=["config"])
            bridge.run_discovery(credential_pk=bridge.credential_id, generation=bridge.discovery_generation)

    # Operator verbs are actor-admitted (GraphQL is their only caller); Resume is admin-only.
    operator = create_platform_admin(f"lock-order-{verb}")
    recipient = create_user(f"lock-order-{verb}-recipient")

    def operate():
        with actor_context(operator):
            row = Integration.objects.get(pk=bridge.pk)
            if verb == "transfer_ownership":
                row.with_actor(operator).transfer_ownership(recipient)
            else:
                getattr(row, verb)()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test child-only worker lock"), transaction.atomic():
            # Hold only the concrete table to expose a parent-before-child inversion.
            ConnectionBridge.objects.select_for_update(of=("self",)).get(pk=bridge.pk)
            contender, pid = submit(pool, operate)
            wait_for_lock(pid, contender)
            # This remains available while the parent-addressed verb waits for the child.
            Integration.objects.lock_if_supported().get(pk=bridge.pk)
        contender.result(timeout=10)
    bridge.refresh_from_db()
    assert bridge.owner_id == recipient.pk if verb == "transfer_ownership" else bridge.discovery_pending


def test_reconsent_waits_for_resume_without_locking_its_source_credential(bridge):
    with system_context(reason="test derived connection before resume"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert bridge.run_discovery(credential_pk=source.pk, generation=bridge.discovery_generation)["ok"]
        bridge.pause()
        assert bridge.credential_id != source.pk and bridge.discovery_credential_id == source.pk
    operator = create_platform_admin("reconsent-resume")

    def reconsent():
        return Credential.objects.upsert_for_user(
            source.user, source.oauth_client, source.kind, {"api_key": "renewed"},
        ).pk

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test resume wins integration lock"), transaction.atomic():
            bridge.lock_capability()
            contender, pid = submit(pool, reconsent)
            wait_for_lock(pid, contender)
            # Fail in finite time on the old credential -> integration inversion.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '3s'")
            with actor_context(operator):
                Integration.objects.get(pk=bridge.pk).resume()
        assert contender.result(timeout=10) == source.pk
    with system_context(reason="test resumed connection after reconsent"):
        bridge.refresh_from_db()
        assert bridge.lifecycle == "connected" and bridge.discovery_pending
        assert bridge.credential_id == source.pk
        source.refresh_from_db()
        assert source.reveal()["api_key"] == "renewed"


def test_a_replaced_secret_waits_for_the_integration_without_locking_its_credential(bridge):
    with system_context(reason="test derived connection before re-entry"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert bridge.run_discovery(credential_pk=source.pk, generation=bridge.discovery_generation)["ok"]
        bridge.refresh_from_db()
        generation = bridge.discovery_generation

    def re_enter():
        with system_context(reason="test independent re-entry"):
            Credential.objects.get(pk=source.pk).replace_material({"api_key": "re-entered"})

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test attachment holds the integration lock"), transaction.atomic():
            bridge.lock_capability()
            contender, pid = submit(pool, re_enter)
            wait_for_lock(pid, contender)
            # Re-attaching the source grant takes its key-share lock: fail in finite
            # time on a credential -> integration inversion.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '3s'")
            bridge.await_discovery()
        contender.result(timeout=10)
    with system_context(reason="test re-entry after the attachment"):
        bridge.refresh_from_db()
        source.refresh_from_db()
    assert source.reveal() == {"api_key": "re-entered"}
    assert bridge.credential_id == source.pk
    assert bridge.discovery_generation == generation + 2 and bridge.discovery_pending


def test_a_credential_delete_waits_for_the_integration_deriving_from_it_without_locking_the_credential(bridge):
    with system_context(reason="test derived connection before the delete"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert bridge.run_discovery(credential_pk=source.pk, generation=bridge.discovery_generation)["ok"]

    def lock_for_delete():
        with system_context(reason="test independent credential delete"), transaction.atomic():
            return Credential.objects.get(pk=source.pk).lock_for_delete().pk

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test attachment holds the integration lock"), transaction.atomic():
            bridge.lock_capability()
            contender, pid = submit(pool, lock_for_delete)
            wait_for_lock(pid, contender)
            # The delete waits on the integration it releases without holding the credential.
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL lock_timeout = '3s'")
            Credential.objects.lock_if_supported().get(pk=source.pk)
        assert contender.result(timeout=10) == source.pk
