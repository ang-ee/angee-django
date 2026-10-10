"""PostgreSQL connection discovery races through independent row-lock owners."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection, transaction
from rebac import system_context

from angee.integrate.discovery import ConnectionDiscovery
from tests.conftest import Credential
from tests.integrate_fixtures import ConnectionBridge
from tests.test_decisions_concurrency import submit, wait_for_lock
from tests.test_integrate_binding import bridge as bridge
from tests.test_integrate_binding import connection_settings as connection_settings
from tests.test_integrate_binding import sent_tasks as sent_tasks

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("composed_tables"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_attach_commits_before_blocked_finish_and_rejects_all_stale_side_effects(bridge):
    old_credential, generation = bridge.credential_id, bridge.binding_generation
    with system_context(reason="test replacement grant"):
        replacement = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="new connection", material={"api_key": "fake"},
        )

    def finish():
        with system_context(reason="test independent discovery finish"):
            row = ConnectionBridge.objects.get(pk=bridge.pk)
            return row.finish_binding(
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
    assert bridge.binding_generation == generation + 1 and bridge.binding_pending


def test_finish_commits_before_blocked_attach_and_new_attachment_clears_completion(bridge):
    credential_pk, generation = bridge.credential_id, bridge.binding_generation

    def attach():
        with system_context(reason="test independent connection attach"):
            row = ConnectionBridge.objects.get(pk=bridge.pk)
            row.attach_credential(row.credential)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="test discovery wins"), transaction.atomic():
            ConnectionBridge.objects.lock_if_supported().get(pk=bridge.pk)
            assert bridge.finish_binding(
                credential_pk=credential_pk, generation=generation,
                discovery=ConnectionDiscovery(data={"display_name": "First resource"}),
            )
            contender, pid = submit(pool, attach)
            wait_for_lock(pid, contender)
        contender.result(timeout=10)
    bridge.refresh_from_db()
    assert bridge.display_name == "First resource"
    assert bridge.binding_generation == generation + 1 and bridge.binding_pending
    assert bridge.binding_completed_at is None and bridge.next_sync_at is None
