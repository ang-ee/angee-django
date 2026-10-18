"""Connection discovery owns attachment, fenced apply and bounded retries."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
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
from rebac.backends import backend

from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.identity import public_id_of
from angee.integrate import scheduler, tasks
from angee.integrate.connect import complete_account_connect, complete_external_account_link
from angee.integrate.constants import DISCOVERY_ATTEMPT_LIMIT, DISCOVERY_TASK
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.oauth.errors import OAuthFlowError
from angee.integrate.schema import (
    IntegrationActionMutation,
    IntegrationCredentialMutation,
    IntegrationWriteBackend,
    _attach_completed_integration,
    apply_integration_patch_fields,
    save_provided_fields,
)
from angee.integrate.session_runner import run_bridge_session_job
from angee.integrate.signals import discovery_finished
from angee.integrate.streams import BridgeSyncError
from angee.jobs.celery import app as celery_app
from angee.testing.permissions import install_permission_schema
from tests.conftest import Credential, create_platform_admin, execute_schema, make_addon, make_integration, result_data
from tests.integrate_fixtures import CONNECTION_CLASSES, ConnectionBridge, LiveConnectionAdapter

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
    return bridge.run_discovery(credential_pk=bridge.credential_id, generation=bridge.discovery_generation)


def connection_info(bridge):
    @strawberry_django.type(ConnectionBridge)
    class CapabilityType(AngeeNode):
        pass

    resource = hasura_model_resource(
        CapabilityType, model=ConnectionBridge, filterable=("id",), sortable=("id",), aggregatable=("id",),
        insert=False, update=False, delete=False,
    )
    schema = GraphQLSchemas([make_addon(schemas={
        "console": {"query": [resource.query], "mutation": [IntegrationActionMutation]},
    })]).build("console")
    return SimpleNamespace(context=SimpleNamespace(request=SimpleNamespace(user=bridge.owner)), schema=schema)


def test_attachment_defers_one_captured_task_until_commit_and_rolls_back(bridge, sent_tasks):
    original = bridge.discovery_generation
    sent_tasks.clear()
    with system_context(reason="test captured connection dispatch"):
        with transaction.atomic():
            bridge.attach_credential(bridge.credential)
            captured = bridge.discovery_generation
            assert sent_tasks == []
        assert captured == original + 1
        assert [name for name, _ in sent_tasks] == [DISCOVERY_TASK]
        assert sent_tasks[0][1]["kwargs"] == {
            "integration_pk": bridge.pk, "credential_pk": bridge.credential_id, "generation": captured,
        }
        sent_tasks.clear()
        with pytest.raises(RuntimeError), transaction.atomic():
            bridge.attach_credential(bridge.credential)
            raise RuntimeError("rollback")
        bridge.refresh_from_db()
    assert sent_tasks == []
    assert bridge.discovery_generation == captured


def test_apply_fences_credential_and_generation_before_any_domain_write(bridge):
    with system_context(reason="test stale discovery"):
        credential_pk, generation = bridge.credential_id, bridge.discovery_generation
        other = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="replacement", material={"api_key": "fake"},
        )
        bridge.attach_credential(other)
        for stale_generation in (generation, bridge.discovery_generation):
            assert not bridge.finish_discovery(
                credential_pk=credential_pk, generation=stale_generation,
                discovery=ConnectionDiscovery(data={"display_name": "Stale result"}),
            )
        assert bridge.display_name == ""
        assert settle(bridge)["ok"] is True
    assert bridge.display_name == "Discovered resource"
    assert bridge.discovery_ready and not bridge.discovery_pending
    assert bridge.next_sync_at is not None


@pytest.mark.parametrize("path", ["mutation", "patch", "hasura", "connect"])
def test_all_credential_attach_paths_enter_discovery(bridge, path):
    with system_context(reason="test initial discovery"):
        assert settle(bridge)["ok"] is True
        before, credential = bridge.discovery_generation, bridge.credential
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
    assert bridge.discovery_generation == before + 1
    assert bridge.discovery_pending and bridge.discovery_completed_at is None


def test_combined_patch_preserves_other_values_and_starts_discovery_once(bridge):
    with system_context(reason="test combined connection patch"):
        before = bridge.discovery_generation
        data = SimpleNamespace(
            display_name="Operator label", vendor=strawberry.UNSET, owner=strawberry.UNSET,
            account=strawberry.UNSET, credential=public_id_of(bridge.credential), lifecycle="connected",
        )
        fields = apply_integration_patch_fields(bridge, data, reason="test combined patch")
        save_provided_fields(bridge, fields)
        bridge.refresh_from_db()
    assert bridge.display_name == "Operator label"
    assert bridge.discovery_generation == before + 1 and bridge.discovery_pending


@pytest.mark.parametrize(("derived", "disconnected"), [(False, False), (False, True), (True, False)])
def test_oauth_completion_preserves_discovery_for_the_same_identity(
    bridge, sent_tasks, monkeypatch, derived, disconnected,
):
    with system_context(reason="test connect callback"):
        client = bridge.credential.oauth_client
        client.client_id = "application"
        client.client_secret = "fake-secret"
        client.token_endpoint = "https://provider.example/token"
        client.authorize_endpoint = "https://provider.example/auth"
        client.userinfo_endpoint = "https://provider.example/profile"
        client.save(update_fields=[
            "client_id", "client_secret", "token_endpoint", "authorize_endpoint", "userinfo_endpoint",
        ])
        completion = complete_external_account_link(
            client, user=bridge.owner, external_id="connected-account", tokens={"access_token": "fake"},
            claims={"sub": "connected-account"},
        )
        bridge.attach_credential(completion.credential)
        if derived:
            bridge.config = {"derived": True}
            bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        previous_credential_pk = bridge.credential_id
        if disconnected:
            bridge.disconnect()
        generation = bridge.discovery_generation
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
    assert bridge.discovery_generation == generation + int(derived or disconnected)
    assert bridge.lifecycle == "connected"
    if derived or disconnected:
        assert bridge.discovery_pending and not bridge.discovery_ready
        assert bridge.credential_id == completed.credential.pk
        assert len(sent_tasks) == 1
        if derived:
            assert not Credential.objects.sudo(reason="test old derived token disposed").filter(
                pk=previous_credential_pk,
            ).exists()
    else:
        assert bridge.discovery_ready
        assert sent_tasks == []


def test_reconsent_preserves_direct_attachments_and_rediscovers_derived_credentials(bridge):
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
        before = {row.pk: row.discovery_generation for row in (bridge, sibling)}
        complete_external_account_link(
            client, user=owner, external_id="account-one",
            tokens={"access_token": "rotated"}, claims={"sub": "account-one"},
        )
        bridge.refresh_from_db()
        sibling.refresh_from_db()
        assert bridge.discovery_generation == before[bridge.pk]
        assert sibling.discovery_generation == before[sibling.pk] + 1
        assert sibling.discovery_pending and not sibling.discovery_ready
        assert sibling.credential_id == completion.credential.pk
        assert sibling.lifecycle == "paused"
        with pytest.raises(OAuthFlowError) as refused:
            complete_external_account_link(
                client, user=owner, external_id="account-two",
                tokens={"access_token": "other"}, claims={"sub": "account-two"},
            )
        assert refused.value.code == "account_switch_refused"


def test_derived_material_applies_after_fencing_and_retains_the_source_for_reconnect(bridge):
    with system_context(reason="test derived connection"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"] is True
        assert bridge.credential_id != source.pk and bridge.discovery_credential_id == source.pk
        bridge.pause()
        bridge.connect()
        assert bridge.credential_id == source.pk and bridge.discovery_pending
        assert settle(bridge)["ok"] is True
        assert Credential.objects.filter(user=bridge.owner, name=f"Resource {bridge.pk}").count() == 1


@pytest.mark.parametrize("state", ["pending", "ready", "disconnected"])
def test_retry_discovery_refuses_states_outside_its_admission(bridge, state):
    with system_context(reason="test retry admission"):
        if state == "ready":
            assert settle(bridge)["ok"]
        elif state == "disconnected":
            bridge.disconnect()
        generation = bridge.discovery_generation
        assert not bridge.can_retry_discovery()
        with pytest.raises(ValidationError, match="cannot retry discovery"):
            bridge.retry_discovery()
        bridge.refresh_from_db()
        assert bridge.discovery_generation == generation


def test_oauth_connect_admission_requires_the_owner_and_does_not_compete_with_retry(bridge, monkeypatch):
    monkeypatch.setattr(type(bridge), "is_oauth_connectable", lambda row: True)
    with system_context(reason="test connect admission"):
        # A healthy attached grant uses discovery recovery, never new consent.
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        settle(bridge)
        bridge.refresh_from_db()
        assert not bridge.can_connect(bridge.owner)
    with actor_context(bridge.owner):
        assert bridge.can_retry_discovery()
    with system_context(reason="test connect admission without credential"):
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
@pytest.mark.parametrize("actor_kind", ["owner", "admin"])
def test_retryable_discovery_is_durable_and_bounded_and_operator_retry_resets_it(bridge, mode, actor_kind):
    with system_context(reason="test bounded connection retries"):
        bridge.config = {"discovery": mode}
        bridge.save(update_fields=["config"])
        for attempt in range(DISCOVERY_ATTEMPT_LIMIT):
            started = timezone.now()
            settle(bridge)
            bridge.refresh_from_db()
            assert bridge.discovery_attempts == attempt + 1
            if attempt < DISCOVERY_ATTEMPT_LIMIT - 1:
                minimum = timedelta(hours=1) if mode == "quota" else timedelta(minutes=2 ** attempt)
                assert bridge.discovery_retry_at >= started + minimum
                bridge.discovery_retry_at = timezone.now() - timedelta(seconds=1)
                bridge.save(update_fields=["discovery_retry_at"])
        assert not bridge.discovery_pending and not bridge.discovery_ready
        assert bridge.runtime_status == "error" and bridge.next_sync_at is None
        generation = bridge.discovery_generation
        assert not bridge.can_retry_discovery()
        with pytest.raises(ValidationError, match="cannot retry discovery"):
            bridge.retry_discovery()
        bridge.refresh_from_db()
        assert bridge.discovery_generation == generation
    actor = bridge.owner if actor_kind == "owner" else create_platform_admin("discovery-operator")
    with actor_context(actor):
        assert bridge.can_retry_discovery()
        bridge.retry_discovery()
        assert bridge.discovery_attempts == 0 and bridge.discovery_pending
        assert bridge.discovery_generation == generation + 1
        assert bridge.lifecycle == "connected" and bridge.runtime_status == "pending"
        assert not bridge.can_retry_discovery()


def test_terminal_refusal_is_safe_and_resume_reruns_discovery(bridge):
    with system_context(reason="test discovery recovery"):
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["reason"] == "discovery-failed"
        assert bridge.last_error == "Integration operation failed."
        bridge.pause()
        bridge.connect()
        assert bridge.discovery_pending and bridge.discovery_attempts == 0
        assert bridge.discovery_completed_at is None


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
                bridge.await_discovery()
                assert bridge.subscription_state["desired"] == bridge.LiveState.STOPPED
                assert bridge.subscription_state["own_id"] == "resource"
                assert settle(bridge)["reason"] == "locked"
                assert bridge.discovery_attempts == 0
            finally:
                release.set()
            future.result(timeout=10)
        assert settle(bridge)["ok"] is True


def test_pending_discovery_preserves_dispatched_work_and_gates_every_admission(bridge):
    with system_context(reason="test discovery and workflow ownership"):
        assert settle(bridge)["ok"]
        assert bridge.claim_dispatch(123)
        bridge.await_discovery()
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
        bridge.await_discovery()
        assert bridge.last_used_at == at and bridge.last_used_status == "ok"
        bridge.attach_credential(None)
        assert not bridge.discovery_pending
        assert bridge.runtime_status == "error" and bridge.last_error == "No credential is attached."


def test_finished_signal_is_sent_once_on_commit_after_apply(bridge):
    seen = []

    def receive(sender, *, instance, **kwargs):
        assert not transaction.get_connection().in_atomic_block
        seen.append((sender, instance.display_name))

    discovery_finished.connect(receive, weak=False)
    try:
        with system_context(reason="test committed discovery signal"), transaction.atomic():
            result = ConnectionDiscovery(data={"display_name": "Committed resource"})
            assert bridge.finish_discovery(
                credential_pk=bridge.credential_id, generation=bridge.discovery_generation, discovery=result,
            )
            assert seen == []
        assert seen == [(ConnectionBridge, "Committed resource")]
        with system_context(reason="test duplicate settlement"):
            assert not bridge.finish_discovery(
                credential_pk=bridge.credential_id, generation=bridge.discovery_generation, discovery=result,
            )
        assert len(seen) == 1
    finally:
        discovery_finished.disconnect(receive)


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

    monkeypatch.setattr(scheduler, "enqueue_pending_discoveries", fail)
    monkeypatch.setattr(scheduler, "enqueue_due_bridges", lambda: seen.append("poll"))
    tasks.sync_due_bridges()
    assert seen == ["poll"]


def test_credential_only_attachment_settles_synchronously_without_a_task(sent_tasks):
    with system_context(reason="test credential-only attachment"):
        integration = make_integration("credential-only")
        integration.attach_credential(integration.credential)
        assert integration.discovery_ready and integration.discovery_completed_at is not None
        assert not integration.discovery_pending
    assert sent_tasks == []



def test_same_lifecycle_preserves_discovery_and_poll_schedule(bridge, sent_tasks):
    with system_context(reason="test idempotent lifecycle"):
        assert settle(bridge)["ok"]
        before = (bridge.discovery_generation, bridge.discovery_completed_at, bridge.next_sync_at)
        sent_tasks.clear()
        bridge.set_lifecycle("connected")
        bridge.refresh_from_db()
        assert (bridge.discovery_generation, bridge.discovery_completed_at, bridge.next_sync_at) == before
        assert bridge.discovery_ready and sent_tasks == []


def test_credential_only_live_attachment_keeps_the_session_desire(sent_tasks, monkeypatch):
    monkeypatch.setattr(LiveConnectionAdapter, "requires_connection_discovery", False)
    with system_context(reason="test live credential-only attachment"):
        row = make_integration(
            "live-no-discovery", model=ConnectionBridge, connection_impl="live",
            subscription_state={"desired": ConnectionBridge.LiveState.LIVE, "own_id": "resource"},
        )
        row.attach_credential(row.credential)
        assert row.discovery_ready
        assert row.subscription_state["desired"] == row.LiveState.LIVE
        assert sent_tasks == []


def test_non_admin_owner_can_retry_discovery_but_cannot_resume(bridge):
    with system_context(reason="test failed discovery for owner retry"):
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["reason"] == "discovery-failed"
        bridge.pause()
    with actor_context(bridge.owner):
        assert bridge.can_retry_discovery()
        assert not bridge.can_resume()
        with pytest.raises(ValidationError, match="cannot resume"):
            bridge.resume()
    info = connection_info(bridge)
    refused = execute_schema(info.schema, f'''mutation {{
      mark_integration_connected(id: "{bridge.sqid}") {{ ok }}
    }}''', request=info.context.request)
    assert refused.errors is not None
    result = result_data(execute_schema(info.schema, f'''mutation {{
      retry_discovery(id: "{bridge.sqid}") {{ ok }}
    }}''', request=info.context.request))["retry_discovery"]
    assert result["ok"]
    bridge.refresh_from_db()
    assert bridge.discovery_pending and bridge.discovery_attempts == 0
    assert bridge.lifecycle == "paused"


def test_retry_discovery_authorizes_its_declared_permission(bridge):
    with system_context(reason="test owner retry without generic write"):
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["reason"] == "discovery-failed"
    info = connection_info(bridge)
    active = backend()
    original = active.schema()
    resource_type = "integrate/integration"
    definition = original.get_definition(resource_type)
    resume = original.get_permission(resource_type, "resume")
    narrowed = replace(definition, permissions=tuple(
        replace(permission, expression=resume.expression, raw_text=resume.raw_text)
        if permission.name == "write" else permission
        for permission in definition.permissions
    ))
    install_permission_schema(replace(original, definitions=[
        narrowed if item.resource_type == resource_type else item for item in original.definitions
    ]), active=active)
    try:
        with actor_context(bridge.owner):
            assert not bridge.has_access("write")
            assert bridge.can_retry_discovery()
        result = result_data(execute_schema(info.schema, f'''mutation {{
          retry_discovery(id: "{bridge.sqid}") {{ ok }}
        }}''', request=info.context.request))["retry_discovery"]
        assert result["ok"]
        bridge.refresh_from_db()
        assert bridge.discovery_pending
    finally:
        install_permission_schema(original, active=active)


def test_disconnect_source_grant_releases_derived_material(bridge):
    with system_context(reason="test disconnect derived connection"), transaction.atomic():
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
    with system_context(reason="test discover derived connection"):
        assert settle(bridge)["ok"]
        derived_pk = bridge.credential_id
        assert derived_pk != source.pk
        with transaction.atomic():
            Credential.objects.prepare_disconnect(source)
            Credential.objects.filter(pk=source.pk).delete()
        bridge.refresh_from_db()
        assert bridge.lifecycle == "disconnected"
        assert bridge.runtime_status == "ok" and bridge.last_error == ""
        assert bridge.credential_id is None and bridge.discovery_credential_id is None
        assert not Credential.objects.filter(pk__in=[source.pk, derived_pk]).exists()


def test_live_reconciler_and_queued_session_refuse_incomplete_discovery(bridge, monkeypatch):
    with system_context(reason="test pairing health during discovery"):
        bridge.connection_impl = "live"
        bridge.subscription_state = {"desired": bridge.LiveState.LIVE, "own_id": "resource"}
        bridge.save(update_fields=["connection_impl", "subscription_state"])
        bridge.report_status("ok")  # Pairing can report OK while discovery remains pending.
    monkeypatch.setattr(tasks, "models_with", lambda **kwargs: [ConnectionBridge])
    assert tasks.ensure_bridge_sessions()["dispatched"] == 0
    result = run_bridge_session_job(ConnectionBridge._meta.label, bridge.pk, stop_event=Event())
    assert result["reason"] == "discovery-incomplete"


def test_unclassified_discovery_and_scheduler_failures_log_tracebacks(bridge, caplog, monkeypatch):
    with system_context(reason="test discovery diagnostics"):
        bridge.config = {"discovery": "refusal"}
        bridge.save(update_fields=["config"])
        settle(bridge)
    assert any(record.exc_info and "Connection discovery failed" in record.message for record in caplog.records)

    def fail():
        raise RuntimeError("scheduler diagnostic")

    monkeypatch.setattr(scheduler, "enqueue_pending_discoveries", fail)
    monkeypatch.setattr(scheduler, "enqueue_due_bridges", lambda: None)
    tasks.sync_due_bridges()
    assert any(record.exc_info and "scheduler step" in record.message for record in caplog.records)


def test_discovery_stops_when_its_source_credential_was_deleted(bridge, sent_tasks):
    with system_context(reason="test lost discovery source"):
        source = bridge.credential
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived_pk = bridge.credential_id
        Credential.objects.filter(pk=source.pk).delete()
        bridge.refresh_from_db()
        assert bridge.discovery_credential_id is None
        sent_tasks.clear()
        bridge.await_discovery()
        assert bridge.credential_id == derived_pk and bridge.discovery_credential_id is None
        assert not bridge.discovery_ready and not bridge.discovery_pending
        assert bridge.runtime_status == "error"
        assert bridge.last_error == "The source credential for connection discovery is no longer available."
        assert sent_tasks == []
