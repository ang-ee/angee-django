"""Connection discovery owns attachment, fenced apply and bounded retries."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import httpx2
import pytest
import strawberry
import strawberry_django
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rebac import actor_context, system_context

from angee.base.identity import public_id_of
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.integrate import scheduler, tasks
from angee.integrate.connect import complete_account_connect, complete_external_account_link
from angee.integrate.constants import BINDING_ATTEMPT_LIMIT, BINDING_TASK
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.schema import (
    IntegrationCredentialMutation,
    IntegrationWriteBackend,
    _attach_completed_integration,
    apply_integration_patch_fields,
    save_provided_fields,
)
from angee.integrate.signals import binding_finished
from angee.integrate.streams import BridgeSyncError
from angee.jobs.celery import app as celery_app
from tests.conftest import Credential, create_platform_admin, make_addon, make_integration
from tests.integrate_fixtures import CONNECTION_CLASSES, ConnectionBridge

pytestmark = pytest.mark.usefixtures("composed_tables")


@pytest.fixture(autouse=True)
def connection_settings():
    with override_settings(
        ANGEE_TEST_CONNECTION_CLASSES=CONNECTION_CLASSES,
    ):
        yield


@pytest.fixture
def sent_tasks(monkeypatch):
    sent = []
    monkeypatch.setattr(celery_app, "send_task", lambda name, **options: sent.append((name, options)))
    return sent


@pytest.fixture
def bridge(sent_tasks):
    return make_integration("connection-resource", model=ConnectionBridge)


def settle(bridge):
    return bridge.run_binding(credential_pk=bridge.credential_id, generation=bridge.binding_generation)


def connection_info(bridge):
    @strawberry_django.type(ConnectionBridge)
    class CapabilityType(AngeeNode):
        pass

    resource = hasura_model_resource(
        CapabilityType, model=ConnectionBridge, filterable=("id",), sortable=("id",), aggregatable=("id",),
        insert=False, update=False, delete=False,
    )
    schema = GraphQLSchemas([make_addon(schemas={"console": {"query": [resource.query]}})]).build("console")
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=bridge.owner)), schema=schema)


def test_attachment_defers_one_captured_task_until_commit_and_rolls_back(bridge, sent_tasks):
    original = bridge.binding_generation
    sent_tasks.clear()
    with system_context(reason="test captured connection dispatch"):
        with transaction.atomic():
            bridge.attach_credential(bridge.credential)
            captured = bridge.binding_generation
            assert sent_tasks == []
        assert captured == original + 1
        assert [name for name, _ in sent_tasks] == [BINDING_TASK]
        assert sent_tasks[0][1]["kwargs"] == {
            "integration_pk": bridge.pk, "credential_pk": bridge.credential_id, "generation": captured,
        }
        sent_tasks.clear()
        with pytest.raises(RuntimeError), transaction.atomic():
            bridge.attach_credential(bridge.credential)
            raise RuntimeError("rollback")
        bridge.refresh_from_db()
    assert sent_tasks == []
    assert bridge.binding_generation == captured


def test_apply_fences_credential_and_generation_before_any_domain_write(bridge):
    with system_context(reason="test stale discovery"):
        credential_pk, generation = bridge.credential_id, bridge.binding_generation
        other = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="replacement", material={"api_key": "fake"},
        )
        bridge.attach_credential(other)
        for stale_generation in (generation, bridge.binding_generation):
            assert not bridge.finish_binding(
                credential_pk=credential_pk, generation=stale_generation,
                discovery=ConnectionDiscovery(data={"display_name": "Stale result"}),
            )
        assert bridge.display_name == ""
        assert settle(bridge)["ok"] is True
    assert bridge.display_name == "Discovered resource"
    assert bridge.binding_ready and not bridge.binding_pending
    assert bridge.next_sync_at is not None


@pytest.mark.parametrize("path", ["mutation", "patch", "hasura", "connect"])
def test_all_credential_attach_paths_enter_discovery(bridge, path):
    with system_context(reason="test initial discovery"):
        assert settle(bridge)["ok"] is True
        before, credential = bridge.binding_generation, bridge.credential
    if path in ("mutation", "hasura"):
        info = connection_info(bridge)
        with actor_context(bridge.owner):
            if path == "mutation":
                IntegrationCredentialMutation().attach_integration_credential(
                    info, ConnectionBridge._meta.label,
                    PublicID(public_id_of(bridge)), PublicID(public_id_of(credential)),
                )
            else:
                IntegrationWriteBackend(ConnectionBridge).update(
                    info, public_id_of(bridge),
                    {"credential": public_id_of(credential), "display_name": "Operator label"},
                )
    else:
        with system_context(reason="test credential write"):
            if path == "patch":
                data = SimpleNamespace(
                    vendor=strawberry.UNSET, owner=strawberry.UNSET, account=strawberry.UNSET,
                    credential=public_id_of(credential), lifecycle=strawberry.UNSET,
                )
                apply_integration_patch_fields(bridge, data, reason="test connection patch")
            else:
                bridge.pause()
                bridge.connect(credential=credential)
    bridge.refresh_from_db()
    assert bridge.binding_generation == before + 1
    assert bridge.binding_pending and bridge.binding_completed_at is None


def test_combined_patch_preserves_other_values_and_starts_discovery_once(bridge):
    with system_context(reason="test combined connection patch"):
        before = bridge.binding_generation
        data = SimpleNamespace(
            display_name="Operator label", vendor=strawberry.UNSET, owner=strawberry.UNSET,
            account=strawberry.UNSET, credential=public_id_of(bridge.credential), lifecycle="connected",
        )
        fields = apply_integration_patch_fields(bridge, data, reason="test combined patch")
        save_provided_fields(bridge, fields)
        bridge.refresh_from_db()
    assert bridge.display_name == "Operator label"
    assert bridge.binding_generation == before + 1 and bridge.binding_pending


def test_oauth_completion_reattaches_through_the_owner_without_dispatching_twice(bridge, sent_tasks, monkeypatch):
    with system_context(reason="test connect callback"):
        client = bridge.credential.oauth_client
        client.client_id = "application"
        client.token_endpoint = "https://provider.example/token"
        client.authorize_endpoint = "https://provider.example/auth"
        client.userinfo_endpoint = "https://provider.example/profile"
        client.save(update_fields=["client_id", "token_endpoint", "authorize_endpoint", "userinfo_endpoint"])
        completion = complete_external_account_link(
            client, user=bridge.owner, external_id="connected-account", tokens={"access_token": "fake"},
            claims={"sub": "connected-account"},
        )
        bridge.attach_credential(completion.credential)
        assert settle(bridge)["ok"]
        generation = bridge.binding_generation
        token, _record = state.issue(
            client, "https://app.example/callback", user_id=str(bridge.owner.pk), flow=state.StateFlow.CONNECT,
            integration_id=bridge.sqid,
        )
    original = OAuthClientProtocol.__init__

    def with_transport(self, oauth_client):
        original(self, oauth_client)
        self._transport = httpx2.MockTransport(lambda request: httpx2.Response(
            200, json={"sub": "connected-account"} if request.url.path == "/profile" else {"access_token": "rotated"},
        ))

    monkeypatch.setattr(OAuthClientProtocol, "__init__", with_transport)
    info = connection_info(bridge)
    sent_tasks.clear()
    with actor_context(bridge.owner):
        completed = complete_account_connect(
            client, code="code", state_token=token, redirect_uri="https://app.example/callback",
        )
        _attach_completed_integration(info, completed.integration_id, bridge.owner, completed.credential)
    bridge.refresh_from_db()
    assert bridge.binding_generation == generation + 1 and bridge.binding_pending
    assert [name for name, _options in sent_tasks] == [BINDING_TASK]


def test_shared_grant_rotation_rebinds_all_attachments_and_refuses_account_switch(bridge):
    with system_context(reason="test shared grant"):
        client, owner = bridge.credential.oauth_client, bridge.owner
        completion = complete_external_account_link(
            client, user=owner, external_id="account-one",
            tokens={"access_token": "fake"}, claims={"sub": "account-one"},
        )
        bridge.attach_credential(completion.credential)
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=owner, credential=completion.credential,
            lifecycle="paused", config={"derived": True},
        )
        assert settle(sibling)["ok"]
        assert sibling.credential_id != completion.credential.pk
        before = {row.pk: row.binding_generation for row in (bridge, sibling)}
        complete_external_account_link(
            client, user=owner, external_id="account-one",
            tokens={"access_token": "rotated"}, claims={"sub": "account-one"},
        )
        for row in (bridge, sibling):
            row.refresh_from_db()
            assert row.binding_generation == before[row.pk] + 1 and row.binding_pending
        assert sibling.lifecycle == "paused"
        with pytest.raises(IntegrationError, match="external account"):
            complete_external_account_link(
                client, user=owner, external_id="account-two",
                tokens={"access_token": "other"}, claims={"sub": "account-two"},
            )


def test_derived_material_applies_after_fencing_and_retains_the_source_for_reconnect(bridge):
    with system_context(reason="test derived connection"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"] is True
        assert bridge.credential_id != source.pk and bridge.binding_credential_id == source.pk
        bridge.pause()
        bridge.connect()
        assert bridge.credential_id == source.pk and bridge.binding_pending
        assert settle(bridge)["ok"] is True
        assert Credential.objects.filter(user=bridge.owner, name=f"Resource {bridge.pk}").count() == 1


@pytest.mark.parametrize("state", ["pending", "ready", "disconnected"])
def test_retry_discovery_refuses_states_outside_its_admission(bridge, state):
    with system_context(reason="test retry admission"):
        if state == "ready":
            assert settle(bridge)["ok"]
        elif state == "disconnected":
            bridge.disconnect()
        generation = bridge.binding_generation
        assert not bridge.can_retry_binding()
        with pytest.raises(ValidationError, match="cannot retry discovery"):
            bridge.retry_binding()
        bridge.refresh_from_db()
        assert bridge.binding_generation == generation


def test_oauth_connect_admission_requires_the_owner_and_does_not_compete_with_retry(bridge, monkeypatch):
    monkeypatch.setattr(type(bridge), "is_oauth_connectable", lambda row: True)
    with system_context(reason="test connect admission"):
        # A healthy attached grant uses discovery recovery, never new consent.
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        settle(bridge)
        bridge.refresh_from_db()
        assert bridge.can_retry_binding()
        assert not bridge.can_connect(bridge.owner)
        bridge.credential = None
        bridge.save(update_fields=["credential"])
    with actor_context(bridge.owner):
        assert bridge.can_connect()
        bridge.require_connect(bridge.owner)
    visitor = create_platform_admin("connect-visitor")
    with actor_context(visitor):
        assert bridge.has_access("write")
    assert not bridge.can_connect(visitor)
    with pytest.raises(PermissionDenied):
        bridge.require_connect(visitor)


@pytest.mark.parametrize("mode", ["transport", "server", "quota"])
def test_retryable_discovery_is_durable_and_bounded_and_operator_retry_resets_it(bridge, mode):
    with system_context(reason="test bounded connection retries"):
        bridge.config = {"discovery": mode}
        bridge.save(update_fields=["config"])
        for attempt in range(BINDING_ATTEMPT_LIMIT):
            started = timezone.now()
            settle(bridge)
            bridge.refresh_from_db()
            assert bridge.binding_attempts == attempt + 1
            if attempt < BINDING_ATTEMPT_LIMIT - 1:
                minimum = timedelta(hours=1) if mode == "quota" else timedelta(minutes=1)
                assert bridge.binding_retry_at >= started + minimum
                bridge.binding_retry_at = timezone.now() - timedelta(seconds=1)
                bridge.save(update_fields=["binding_retry_at"])
        assert not bridge.binding_pending and not bridge.binding_ready
        assert bridge.runtime_status == "error" and bridge.next_sync_at is None
        bridge.retry_binding()
        assert bridge.binding_attempts == 0 and bridge.binding_pending


def test_terminal_refusal_is_safe_and_resume_reruns_discovery(bridge):
    with system_context(reason="test discovery recovery"):
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["reason"] == "discovery-failed"
        assert bridge.last_error == "Integration operation failed."
        bridge.pause()
        bridge.connect()
        assert bridge.binding_pending and bridge.binding_attempts == 0
        assert bridge.binding_completed_at is None


def test_live_desire_stops_before_lock_contention_without_spending_an_attempt(bridge):
    ready, release = Event(), Event()

    def live_session():
        close_old_connections()
        try:
            with bridge_advisory_lock(bridge) as held:
                assert held
                ready.set()
                assert release.wait(10)
        finally:
            connections.close_all()

    with system_context(reason="test live session contention"):
        bridge.connection_impl = "live"
        bridge.subscription_state = {"desired": bridge.LiveState.LIVE, "own_id": "resource"}
        bridge.save(update_fields=["connection_impl", "subscription_state"])
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(live_session)
            try:
                assert ready.wait(10)
                bridge.await_binding()
                assert bridge.subscription_state["desired"] == bridge.LiveState.STOPPED
                assert bridge.subscription_state["own_id"] == "resource"
                assert settle(bridge)["reason"] == "locked"
                assert bridge.binding_attempts == 0
            finally:
                release.set()
            future.result(timeout=10)
        assert settle(bridge)["ok"] is True


def test_pending_discovery_preserves_dispatched_work_and_gates_every_admission(bridge):
    with system_context(reason="test discovery and workflow ownership"):
        assert settle(bridge)["ok"]
        assert bridge.claim_dispatch(123)
        bridge.await_binding()
        assert bridge.sync_is_dispatched and bridge.sync_run_id == 123
        assert not bridge.claim_dispatch(124)
        assert not bridge.claim_sync(now=timezone.now())
        assert not bridge.mark_sync_started(now=timezone.now())
        assert bridge.settle_dispatch(123, result=1)
        assert bridge.runtime_status == "pending" and bridge.next_sync_at is None


def test_detaching_pending_credential_settles_and_pending_preserves_usage(bridge):
    with system_context(reason="test missing discovery credential"):
        at = timezone.now() - timedelta(days=1)
        bridge.last_used_at, bridge.last_used_status = at, "ok"
        bridge.save(update_fields=["last_used_at", "last_used_status"])
        bridge.await_binding()
        assert bridge.last_used_at == at and bridge.last_used_status == "ok"
        bridge.attach_credential(None)
        assert not bridge.binding_pending
        assert bridge.runtime_status == "error" and bridge.last_error == "No credential is attached."


def test_finished_signal_is_sent_once_on_commit_after_apply(bridge):
    seen = []

    def receive(sender, *, instance, **kwargs):
        assert not transaction.get_connection().in_atomic_block
        seen.append((sender, instance.display_name))

    binding_finished.connect(receive, weak=False)
    try:
        with system_context(reason="test committed discovery signal"), transaction.atomic():
            result = ConnectionDiscovery(data={"display_name": "Committed resource"})
            assert bridge.finish_binding(
                credential_pk=bridge.credential_id, generation=bridge.binding_generation, discovery=result,
            )
            assert seen == []
        assert seen == [(ConnectionBridge, "Committed resource")]
        with system_context(reason="test duplicate settlement"):
            assert not bridge.finish_binding(
                credential_pk=bridge.credential_id, generation=bridge.binding_generation, discovery=result,
            )
        assert len(seen) == 1
    finally:
        binding_finished.disconnect(receive)


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("permanent", [False, True])
def test_stream_partition_errors_retain_the_longest_provider_horizon(bridge, paused, permanent):
    with system_context(reason="test stream retry hints"):
        assert settle(bridge)["ok"]
        bridge.config = {"sync_quota": True, "sync_permanent": permanent}
        bridge.save(update_fields=["config"])
        if paused:
            bridge.pause()
        before = timezone.now()
        with pytest.raises(BridgeSyncError) as raised:
            bridge.run_sync(now=before)
        bridge.refresh_from_db()
    assert raised.value.transient is not permanent and raised.value.retry_after == timedelta(hours=2)
    assert len(raised.value.failures) == 2
    if paused:
        assert bridge.next_sync_at is None
    else:
        assert bridge.next_sync_at >= before + timedelta(hours=2)


def test_scheduler_failure_does_not_skip_regular_polling(monkeypatch):
    seen = []

    def fail():
        raise RuntimeError("broker failure")

    monkeypatch.setattr(scheduler, "enqueue_pending_bindings", fail)
    monkeypatch.setattr(scheduler, "enqueue_due_bridges", lambda: seen.append("poll"))
    tasks.sync_due_bridges()
    assert seen == ["poll"]


def test_credential_only_attachment_settles_synchronously_without_a_task(sent_tasks):
    with system_context(reason="test credential-only attachment"):
        integration = make_integration("credential-only")
        integration.attach_credential(integration.credential)
        assert integration.binding_ready and integration.binding_completed_at is not None
        assert not integration.binding_pending
    assert sent_tasks == []
