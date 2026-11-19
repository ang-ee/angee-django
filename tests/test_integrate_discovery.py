"""Connection discovery owns attachment, fenced apply and bounded retries."""

import importlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event
from types import SimpleNamespace

import httpx2
import pytest
import strawberry_django
from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections, transaction
from django.test import override_settings
from django.utils import timezone
from rebac import actor_context, system_context
from rebac.backends import backend
from rebac.testing import install_schema

from angee.base.models.identity import public_id_of
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.integrate import scheduler, tasks
from angee.integrate.connect import complete_account_connect, complete_external_account_link
from angee.integrate.constants import DISCOVERY_ATTEMPT_LIMIT, DISCOVERY_TASK
from angee.integrate.discovery import ConnectionDiscovery
from angee.integrate.errors import IntegrationError
from angee.integrate.models import CredentialManager
from angee.integrate.oauth import state
from angee.integrate.oauth.client import OAuthClientProtocol
from angee.integrate.schema import (
    IntegrationActionMutation,
    IntegrationCredentialMutation,
    IntegrationWriteBackend,
)
from angee.integrate.session_runner import run_bridge_session_job
from angee.integrate.signals import discovery_finished
from angee.integrate.streams import BridgeSyncError
from angee.integrate.testing.http import mock_transport
from angee.integrate.testing.models import RecordLink, SyncStream
from angee.jobs.celery import app as celery_app
from tests.conftest import (
    Credential,
    Integration,
    addon_schema,
    create_platform_admin,
    create_user,
    make_addon,
    make_integration,
)
from tests.integrate_fixtures import CONNECTION_CLASSES, ConnectionAdapter, ConnectionBridge, LiveConnectionAdapter
from tests.test_integrate_jwt_bearer import neutral_key_file

pytestmark = pytest.mark.django_db(transaction=True)

CREATE_CREDENTIAL = """
    mutation CreateCredential($data: CredentialInput!) {
        create_credential(data: $data) { id }
    }
"""


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
                    info,
                    ConnectionBridge._meta.label,
                    PublicID(public_id_of(bridge)),
                    PublicID(public_id_of(credential)),
                )
            else:
                IntegrationWriteBackend(ConnectionBridge, public_id_fields=("credential",)).update(
                    info,
                    public_id_of(bridge),
                    {"credential": public_id_of(credential), "display_name": "Operator label"},
                )
    else:
        with system_context(reason="test credential write"):
            if path == "patch":
                bridge.prepare_patch({"credential_id": credential.pk})
            else:
                bridge.pause()
                bridge.connect(credential=credential)
    bridge.refresh_from_db()
    assert bridge.discovery_generation == before + 1
    assert bridge.discovery_pending and bridge.discovery_completed_at is None


def test_combined_patch_preserves_other_values_and_starts_discovery_once(bridge):
    with system_context(reason="test combined connection patch"):
        before = bridge.discovery_generation
        info = connection_info(bridge)
        IntegrationWriteBackend(ConnectionBridge, public_id_fields=("credential",)).update(
            info,
            public_id_of(bridge),
            {"display_name": "Operator label", "credential": public_id_of(bridge.credential), "lifecycle": "connected"},
        )
        bridge.refresh_from_db()
    assert bridge.display_name == "Operator label"
    assert bridge.discovery_generation == before + 1 and bridge.discovery_pending


@pytest.mark.parametrize(("derived", "disconnected"), [(False, False), (False, True), (True, False)])
def test_oauth_completion_preserves_discovery_for_the_same_identity(bridge, sent_tasks, derived, disconnected):
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
    sent_tasks.clear()
    provider = mock_transport(lambda request: httpx2.Response(
        200, json={"sub": "connected-account"} if request.url.path == "/profile" else {"access_token": "rotated"},
    ))
    with provider, actor_context(bridge.owner):
        completed = complete_account_connect(
            client, code="code", state_token=token, redirect_uri="https://app.example/callback",
        )
        bridge._meta.apps.get_model("integrate", "Integration").objects.attach_completed_connect(
            completed.integration_id, bridge.owner, completed.credential,
            exposed_model_labels={bridge._meta.label},
        )
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
        generations = {row.pk: row.discovery_generation for row in (bridge, sibling)}
        other = complete_external_account_link(
            client, user=owner, external_id="account-two",
            tokens={"access_token": "other"}, claims={"sub": "account-two"},
        )
        # A second account is a credential of its own; the first account's grant and
        # the integrations it backs are untouched.
        assert other.credential.pk != completion.credential.pk
        completion.credential.refresh_from_db()
        assert completion.credential.reveal()["access_token"] == "rotated"
        for row in (bridge, sibling):
            row.refresh_from_db()
            assert row.discovery_generation == generations[row.pk]
        assert bridge.credential_id == completion.credential.pk


def test_a_replaced_secret_awaits_discovery_again_and_sync_waits_for_it(bridge, sent_tasks):
    with system_context(reason="test re-entered secret"):
        assert settle(bridge)["ok"]
        credential, generation = bridge.credential, bridge.discovery_generation
        sent_tasks.clear()
        credential.replace_material({"api_key": "re-entered"})
        bridge.refresh_from_db()
        assert bridge.discovery_generation == generation + 1
        assert bridge.discovery_pending and not bridge.discovery_ready
        assert bridge.runtime_status == "pending" and bridge.next_sync_at is None
        task = {"integration_pk": bridge.pk, "credential_pk": credential.pk, "generation": generation + 1}
        assert [(name, options["kwargs"]) for name, options in sent_tasks] == [(DISCOVERY_TASK, task)]
        assert not bridge.mark_sync_started(now=timezone.now())
        with pytest.raises(IntegrationError, match="discovery must finish"):
            bridge.request_sync()
        assert settle(bridge)["ok"]
    assert bridge.discovery_ready and bridge.next_sync_at is not None
    assert credential.reveal() == {"api_key": "re-entered"}


def test_a_replaced_secret_commits_only_with_its_rediscovery(bridge, sent_tasks, monkeypatch):
    def refuse(self):
        raise RuntimeError("rediscovery could not start")

    with system_context(reason="test re-entry is one transaction"):
        assert settle(bridge)["ok"]
        credential, generation = bridge.credential, bridge.discovery_generation
        sent_tasks.clear()
        monkeypatch.setattr(ConnectionBridge, "prepare_connection_discovery", refuse)
        with pytest.raises(RuntimeError, match="could not start"):
            credential.replace_material({"api_key": "re-entered"})
        credential.refresh_from_db()
        bridge.refresh_from_db()
    assert credential.reveal() == {"api_key": "x"}
    assert bridge.discovery_generation == generation and bridge.discovery_ready
    assert sent_tasks == []


def test_a_replaced_secret_leaves_a_capability_without_discovery_untouched(sent_tasks, monkeypatch):
    monkeypatch.setattr(LiveConnectionAdapter, "requires_connection_discovery", False)
    with system_context(reason="test re-entry without discovery"):
        row = make_integration(
            "re-entry-no-discovery", model=ConnectionBridge, connection_impl="live",
            subscription_state={"desired": ConnectionBridge.LiveState.LIVE, "own_id": "resource"},
        )
        before = (row.discovery_generation, row.discovery_completed_at, row.next_sync_at, row.runtime_status)
        sent_tasks.clear()
        row.credential.replace_material({"api_key": "re-entered"})
        row.refresh_from_db()
    assert (row.discovery_generation, row.discovery_completed_at, row.next_sync_at, row.runtime_status) == before
    assert row.discovery_ready and row.subscription_state["desired"] == row.LiveState.LIVE
    assert sent_tasks == []


@pytest.mark.parametrize("change", ["refresh", "transient"])
def test_a_refreshed_token_or_transient_material_preserves_discovery(sent_tasks, monkeypatch, change):
    monkeypatch.setattr(
        OAuthClientProtocol, "refresh_token",
        lambda protocol, *, refresh_token: {"access_token": "refreshed", "expires_in": 3600},
    )
    with system_context(reason="test credential-owned material changes"):
        bridge = make_integration(
            f"material-{change}", model=ConnectionBridge, kind="oauth",
            material={"access_token": "granted", "refresh_token": "refresh"},
        )
        assert settle(bridge)["ok"]
        before = (bridge.discovery_generation, bridge.discovery_completed_at, bridge.next_sync_at)
        credential = bridge.credential
        sent_tasks.clear()
        if change == "refresh":
            credential.refresh_now()
        else:
            credential.update_material(password="transient-answer")
        bridge.refresh_from_db()
    assert (bridge.discovery_generation, bridge.discovery_completed_at, bridge.next_sync_at) == before
    assert bridge.discovery_ready and sent_tasks == []
    assert credential.reveal()["access_token"] == ("refreshed" if change == "refresh" else "granted")


@pytest.mark.parametrize("derived", [False, True])
def test_a_replaced_secret_awaits_discovery_for_every_integration_it_serves(bridge, sent_tasks, derived):
    with system_context(reason="test shared re-entered secret"):
        source = bridge.credential
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=source, lifecycle="paused",
            config={"derived": derived},
        )
        for row in (bridge, sibling):
            assert settle(row)["ok"]
        assert (sibling.credential_id != source.pk) is derived
        generations = {row.pk: row.discovery_generation for row in (bridge, sibling)}
        sent_tasks.clear()
        source.replace_material({"api_key": "re-entered"})
        for row in (bridge, sibling):
            row.refresh_from_db()
            assert row.discovery_generation == generations[row.pk] + 1
            assert row.discovery_pending and not row.discovery_ready
            # Material derived from the replaced secret is released with it.
            assert row.credential_id == source.pk
        assert sibling.lifecycle == "paused"
        assert not Credential.objects.filter(name=f"Resource {sibling.pk}").exists()
    assert sorted(options["kwargs"]["integration_pk"] for _name, options in sent_tasks) == [bridge.pk, sibling.pk]


def test_a_secret_of_another_account_reaches_the_operator_as_failed_discovery(bridge):
    with system_context(reason="test re-entered key of another account"):
        bridge.config = {"accounts": {"x": "account-one", "second-key": "account-one", "foreign-key": "account-two"}}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        assert bridge.subscription_state["account"] == "account-one"
        credential = bridge.credential
        credential.replace_material({"api_key": "second-key"})
        bridge.refresh_from_db()
        assert settle(bridge)["ok"] and bridge.discovery_ready
        credential.replace_material({"api_key": "foreign-key"})
        bridge.refresh_from_db()
        assert settle(bridge) == {"ok": False, "reason": "discovery-failed"}
        with pytest.raises(IntegrationError, match="discovery must finish"):
            bridge.request_sync()
    assert bridge.runtime_status == "error"
    assert bridge.last_error == "This key reads account-two, not account-one."
    assert not bridge.discovery_ready and not bridge.discovery_pending
    assert bridge.subscription_state["account"] == "account-one"
    assert bridge.next_sync_at is None


@pytest.mark.parametrize("path", ["connect", "console"])
def test_a_reused_credential_name_awaits_discovery_again_for_the_integration_using_it(bridge, sent_tasks, path):
    with system_context(reason="test credential name in use"):
        credential = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="Shared key", material={"api_key": "first"},
        )
        bridge.attach_credential(credential)
        assert settle(bridge)["ok"]
        generation = bridge.discovery_generation
        sent_tasks.clear()
    if path == "connect":
        # A connect flow keys its credential by name and may change its kind.
        with system_context(reason="test connect reusing the name"), transaction.atomic():
            Credential.objects.create_local_credential(
                bridge.owner, kind="basic_auth", name="Shared key",
                material={"username": "ada@example.com", "password": "re-entered"},
            )
    else:
        schema = addon_schema(
            importlib.import_module("angee.integrate.schema").schemas, "console",
            importlib.import_module("angee.iam.schema").schemas,
        )
        data = {"name": "Shared key", "kind": "static_token", "api_key": "re-entered"}
        data["user"] = public_id_of(bridge.owner)
        admin = create_platform_admin("reuse")
        created = result_data(execute_schema(schema, CREATE_CREDENTIAL, {"data": data}, user=admin))
        assert created["create_credential"]["id"] == public_id_of(credential)
    with system_context(reason="test re-entered by name"):
        bridge.refresh_from_db()
        credential.refresh_from_db()
        assert bridge.credential_id == credential.pk
        assert bridge.discovery_generation == generation + 1 and bridge.discovery_pending
        task = {"integration_pk": bridge.pk, "credential_pk": credential.pk, "generation": generation + 1}
        assert [(name, options["kwargs"]) for name, options in sent_tasks] == [(DISCOVERY_TASK, task)]
        assert not bridge.mark_sync_started(now=timezone.now())
    assert credential.secret_value() == "re-entered"


def test_two_integrations_deriving_one_credential_name_do_not_rediscover_each_other(bridge, sent_tasks):
    with system_context(reason="test one derived name"):
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=bridge.credential, lifecycle="paused",
        )
        for row in (bridge, sibling):
            row.config = {"derived": True, "derived_name": "Shared resource"}
            row.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        generation = bridge.discovery_generation
        sent_tasks.clear()
        # The sibling's discovery stores the same derived credential again.
        assert settle(sibling)["ok"]
        bridge.refresh_from_db()
    assert bridge.discovery_generation == generation and bridge.discovery_ready
    assert sibling.discovery_ready and sibling.credential_id == bridge.credential_id
    assert sent_tasks == []


@pytest.mark.parametrize(
    ("kind", "material", "secret"),
    [("static_token", {"api_key": "x"}, "api_key"), ("basic_auth", {"username": "ada", "password": "x"}, "password")],
)
def test_update_material_merges_transient_keys_and_refuses_the_kinds_secret(kind, material, secret):
    owner = create_user(f"transient-{kind}")
    with system_context(reason="test transient credential material"):
        credential = Credential.objects.create_local_credential(owner, kind=kind, name="Paired", material=material)
        credential.update_material(recovery_key="transient-answer")
        for value in ("rotated", None):
            with pytest.raises(ValueError, match="replace_material"):
                credential.update_material(**{secret: value})
        credential.refresh_from_db()
    assert credential.reveal() == {**material, "recovery_key": "transient-answer"}


@pytest.mark.parametrize(
    ("kind", "material"),
    [
        ("oauth", {"access_token": "granted", "refresh_token": "refresh", "id_token": "identity"}),
        ("app_keys", {"app_id": "app", "app_secret": "secret"}),
        ("ssh_key", {"private_key": "key"}),
    ],
)
def test_update_material_refuses_every_secret_the_kind_stores(kind, material):
    with system_context(reason="test transient material beside every stored secret"):
        credential = make_integration(f"stored-secrets-{kind}", kind=kind, material=material).credential
        credential.update_material(recovery_key="transient-answer")
        for secret in material:
            for value in ("swapped", None):
                with pytest.raises(ValueError, match="replace_material"):
                    credential.update_material(**{secret: value})
        credential.refresh_from_db()
    assert credential.reveal() == {**material, "recovery_key": "transient-answer"}


def test_the_kinds_secret_is_judged_on_the_locked_row_after_a_concurrent_kind_change():
    owner = create_user("changed-kind")
    with system_context(reason="test a kind changed under a stale instance"):
        Credential.objects.create_local_credential(
            owner, kind="static_token", name="Changed kind", material={"api_key": "first"},
        )
        stale = Credential.objects.get(user=owner, name="Changed kind")
        # Another writer re-enters the credential by name as a username and password.
        current = Credential.objects.create_local_credential(
            owner, kind="basic_auth", name="Changed kind",
            material={"username": "ada@example.com", "password": "current"},
        )
        with pytest.raises(ValueError, match="replace_material"):
            stale.update_material(password=None)
        with pytest.raises(ValidationError):
            stale.replace_material({"api_key": "stale"})
        current.refresh_from_db()
    assert (current.kind, current.reveal()) == ("basic_auth", {"username": "ada@example.com", "password": "current"})


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


def test_oauth_connect_admission_requires_the_owner_and_does_not_compete_with_retry(bridge):
    with system_context(reason="test connect admission"):
        client = bridge.credential.oauth_client
        client.client_id, client.client_secret = "application", "fake-secret"
        client.authorize_endpoint = "https://provider.example/auth"
        client.token_endpoint = "https://provider.example/token"
        client.save(update_fields=["client_id", "client_secret", "authorize_endpoint", "token_endpoint"])
        assert bridge.is_oauth_connectable()  # a ready registration admits OAuth connect
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
            with bridge.sync_lock() as held:
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


@pytest.mark.parametrize("change", ["re-entered secret", "attach", "none"])
def test_a_run_in_flight_stops_at_its_next_page_once_discovery_is_pending(bridge, sent_tasks, monkeypatch, change):
    extract, changed = ConnectionAdapter.extract, []

    def between_pages(adapter, stream, page_bound, *, deadline=None):
        if stream.key == "first" and stream.cursor == {"page": 1} and not changed:
            # Another writer, once, between the run's two pages of "first".
            changed.append(change)
            if change == "re-entered secret":
                Credential.objects.get(pk=bridge.credential_id).replace_material({"api_key": "re-entered"})
            elif change == "attach":
                ConnectionBridge.objects.get(pk=bridge.pk).attach_credential(other)
        return extract(adapter, stream, page_bound, deadline=deadline)

    monkeypatch.setattr(ConnectionAdapter, "extract", between_pages)
    with system_context(reason="test a run in flight"):
        other = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="Other key", material={"api_key": "other"},
        )
        bridge.config = {"pages": [["a", "b"], ["c"]]}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        sent_tasks.clear()
        if change == "none":
            assert bridge.run_sync(now=timezone.now()) == 3
        else:
            with pytest.raises(BridgeSyncError) as raised:
                bridge.run_sync(now=timezone.now())
            assert raised.value.transient and raised.value.failures[0].definition.key == "first"
            assert {failure.error.public_message for failure in raised.value.failures} == {
                "Connection discovery must finish before syncing.",
            }
            # The first page stays committed; the second commits nothing.
            stream = SyncStream.objects.current_for_bridge(bridge, "first").get()
            assert stream.cursor == {"page": 1}
            assert sorted(RecordLink.objects.filter(stream=stream).values_list("external_key", flat=True)) == ["a", "b"]
            bridge.refresh_from_db()
            assert bridge.discovery_pending and not bridge.mark_sync_started(now=timezone.now())
            assert [name for name, _options in sent_tasks] == [DISCOVERY_TASK]
            assert settle(bridge)["ok"]
            assert bridge.run_sync(now=timezone.now()) == 1
        stream = SyncStream.objects.current_for_bridge(bridge, "first").get()
        keys = sorted(RecordLink.objects.filter(stream=stream).values_list("external_key", flat=True))
    assert stream.cursor == {"page": 2} and keys == ["a", "b", "c"]


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
    install_schema(replace(original, definitions=[
        narrowed if item.resource_type == resource_type else item for item in original.definitions
    ]), backend=active)
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
        install_schema(original, backend=active)


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


@pytest.mark.parametrize("path", ["kind", "delegated"])
def test_a_re_entry_the_capability_refuses_rolls_back_with_its_refusal(bridge, sent_tasks, monkeypatch, path):
    refusal = "This capability reads with a service key of its own."
    monkeypatch.setattr(
        ConnectionBridge, "credential_blocker",
        lambda self, credential: refusal if credential.kind == "basic_auth" or credential.delegated else None,
    )
    with system_context(reason="test a re-entry the capability refuses"):
        if path == "kind":
            credential = Credential.objects.create_local_credential(
                bridge.owner, kind="static_token", name="Service key", material={"api_key": "first"},
            )
        else:
            credential = Credential.objects.create_local_credential(
                bridge.owner, kind="jwt_bearer", name="Service key", material={"key_file": neutral_key_file()},
            )
        bridge.attach_credential(credential)
        assert settle(bridge)["ok"]
        before = (credential.kind, credential.reveal(), bridge.discovery_generation)
        sent_tasks.clear()
        with pytest.raises(ValidationError, match=refusal):
            if path == "kind":
                Credential.objects.create_local_credential(
                    bridge.owner, kind="basic_auth", name="Service key",
                    material={"username": "ada@example.com", "password": "re-entered"},
                )
            else:
                credential.replace_material({"key_file": neutral_key_file(subject="delegate@example.com")})
        credential.refresh_from_db()
        bridge.refresh_from_db()
    assert (credential.kind, credential.reveal(), bridge.discovery_generation) == before
    assert bridge.discovery_ready and sent_tasks == []


@pytest.mark.parametrize("change", ["arrived", "left"])
def test_a_re_entry_takes_its_locks_again_in_order_when_what_the_credential_serves_changed(
    bridge, sent_tasks, monkeypatch, change,
):
    with system_context(reason="test integrations a credential serves change under a re-entry"):
        assert settle(bridge)["ok"]
        source = bridge.credential
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=source, lifecycle="paused",
        )
        assert settle(sibling)["ok"]
        if change == "left":
            other = Credential.objects.create_local_credential(
                bridge.owner, kind="static_token", name="Other key", material={"api_key": "other"},
            )
            sibling.attach_credential(other)
            assert settle(sibling)["ok"]
        generations = {row.pk: row.discovery_generation for row in (bridge, sibling)}
    served, events = CredentialManager.served_integrations, []

    def first_read_is_stale(manager, credential_pk):
        # The sibling attached the credential (arrived) or let it go (left) just after the first read.
        events.append("read")
        rows = served(manager, credential_pk)
        if events.count("read") > 1:
            return rows
        if change == "arrived":
            return rows.exclude(pk=sibling.pk)
        return rows.model._base_manager.filter(pk__in=(bridge.pk, sibling.pk)).order_by("pk")

    lock = Integration.lock_capability

    def recorded(row):
        events.append(row.pk)
        return lock(row)

    monkeypatch.setattr(CredentialManager, "served_integrations", first_read_is_stale)
    monkeypatch.setattr(Integration, "lock_capability", recorded)
    with system_context(reason="test re-entry under a changed served set"):
        source.replace_material({"api_key": "re-entered"})
    reads = [index for index, event in enumerate(events) if event == "read"]
    assert len(reads) >= 4, events
    # The second read differs: every lock is released to the savepoint and taken again, in key order.
    first = [bridge.pk] if change == "arrived" else [bridge.pk, sibling.pk]
    again = [bridge.pk, sibling.pk] if change == "arrived" else [bridge.pk]
    assert (events[reads[0] + 1:reads[1]], events[reads[2] + 1:reads[3]]) == (first, again)
    with system_context(reason="test which integrations were sent to discover"):
        for row in (bridge, sibling):
            row.refresh_from_db()
    assert bridge.discovery_generation == generations[bridge.pk] + 1
    assert sibling.discovery_generation == generations[sibling.pk] + (change == "arrived")


def test_an_integration_gone_between_the_read_and_its_lock_is_a_changed_set(bridge, sent_tasks, monkeypatch):
    with system_context(reason="test an integration a credential served deleted under a re-entry"):
        assert settle(bridge)["ok"]
        source = bridge.credential
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=source, lifecycle="paused",
        )
        stale = list(Credential.objects.served_integrations(source.pk))
        sibling.delete()
        generation = bridge.discovery_generation
    served, reads = CredentialManager.served_integrations, []

    def first_read_is_stale(manager, credential_pk):
        reads.append(credential_pk)
        return stale if len(reads) == 1 else served(manager, credential_pk)

    monkeypatch.setattr(CredentialManager, "served_integrations", first_read_is_stale)
    with system_context(reason="test re-entry under a vanished integration"):
        source.replace_material({"api_key": "re-entered"})
        bridge.refresh_from_db()
    assert bridge.discovery_generation == generation + 1 and len(reads) > 2


def test_locking_what_a_credential_serves_gives_up_transiently_when_it_keeps_changing(bridge, monkeypatch):
    with system_context(reason="test a served set that never settles"):
        assert settle(bridge)["ok"]
        source = bridge.credential
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=source, lifecycle="paused",
        )
    served, reads = CredentialManager.served_integrations, []

    def always_changed(manager, credential_pk):
        # Each attempt's first read misses the sibling its second read finds.
        reads.append(credential_pk)
        if len(reads) > 50:
            raise AssertionError("The lock retry is unbounded.")
        rows = served(manager, credential_pk)
        return rows.exclude(pk=sibling.pk) if len(reads) % 2 else rows

    monkeypatch.setattr(CredentialManager, "served_integrations", always_changed)
    with system_context(reason="test a re-entry that cannot settle its locks"), transaction.atomic():
        with pytest.raises(IntegrationError) as refused:
            Credential.objects.lock_with_served_integrations(source.pk)
    assert refused.value.transient and len(reads) < 50


def test_an_integration_delete_takes_its_capability_lock_first(bridge, monkeypatch):
    events = []
    lock = Integration.lock_capability

    def recorded(row):
        events.append("capability")
        return lock(row)

    monkeypatch.setattr(Integration, "lock_capability", recorded)
    with system_context(reason="test an integration delete lock order"), transaction.atomic():
        assert Integration.objects.get(pk=bridge.pk).lock_for_delete() is not None
    assert events == ["capability"]


def test_re_entering_the_same_key_file_keeps_discovery(bridge, sent_tasks):
    key_file = neutral_key_file()
    with system_context(reason="test an unchanged key file re-entered"):
        credential = Credential.objects.create_local_credential(
            bridge.owner, kind="jwt_bearer", name="Service key", material={"key_file": key_file},
        )
        bridge.attach_credential(credential)
        assert settle(bridge)["ok"]
        # A token minted from the key is cached beside it, and dropped by the re-entry.
        credential.update_material(scoped_tokens={"read": {"access_token": "minted", "renew_at": 0}})
        generation = bridge.discovery_generation
        sent_tasks.clear()
        credential.replace_material({"key_file": key_file})
        bridge.refresh_from_db()
    assert "scoped_tokens" not in credential.reveal()
    assert bridge.discovery_generation == generation and bridge.discovery_ready and sent_tasks == []


def test_the_integrations_a_credential_serves_are_read_whole_and_a_re_entry_needs_a_transaction(bridge):
    stranger = create_user("served-stranger")
    with actor_context(stranger):
        served = Credential.objects.served_integrations(bridge.credential_id)
        assert list(served.values_list("pk", flat=True)) == [bridge.pk]
    with system_context(reason="test re-entry outside a transaction"), pytest.raises(RuntimeError, match="transaction"):
        with Credential.objects.reenter_secret(bridge.credential_id):
            pass


def test_re_entering_a_credential_discovery_derived_is_refused(bridge, sent_tasks):
    with system_context(reason="test a derived credential re-entered by name"):
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived, generation = bridge.credential, bridge.discovery_generation
        sent_tasks.clear()
        with pytest.raises(ValidationError, match="derived"):
            Credential.objects.create_local_credential(
                bridge.owner, kind="static_token", name=derived.name, material={"api_key": "hand-made"},
            )
        derived.refresh_from_db()
        bridge.refresh_from_db()
    assert derived.reveal() == {"api_key": "fake"}
    assert bridge.credential_id == derived.pk and bridge.discovery_generation == generation
    assert sent_tasks == []


def test_discovery_overwrites_a_credential_of_the_name_it_derives_that_nothing_holds(bridge):
    with system_context(reason="test a derived name left over and unheld"):
        left = Credential.objects.create_local_credential(
            bridge.owner, kind="basic_auth", name=f"Resource {bridge.pk}",
            material={"username": "ada@example.com", "password": "left over"},
        )
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        left.refresh_from_db()
    assert bridge.credential_id == left.pk and bridge.discovery_ready
    assert (left.kind, left.reveal()) == ("static_token", {"api_key": "fake"})


def _hold(credential, holder, owner):
    """Have ``holder`` use ``credential`` other than as discovered material."""

    if holder == "integration":
        return ConnectionBridge.objects.create(vendor=credential_vendor(), owner=owner, credential=credential)
    if holder == "mcp_server":
        return apps.get_model("agents", "MCPServer").objects.create(name="Held key server", credential=credential)
    return apps.get_model("agents", "Agent").objects.create(
        name="Held key agent", owner=owner, inference_credential=credential,
    )


def credential_vendor():
    return apps.get_model("integrate", "Vendor").objects.get_or_create(slug="held", display_name="Held")[0]


@pytest.mark.parametrize("holder", ["integration", "mcp_server", "agent"])
def test_discovery_refuses_a_credential_of_the_name_it_derives_that_something_else_holds(bridge, holder):
    with system_context(reason="test a derived name a used credential holds"):
        held = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name=f"Resource {bridge.pk}", material={"api_key": "held"},
        )
        _hold(held, holder, bridge.owner)
        bridge.config = {"derived": True}
        bridge.save(update_fields=["config"])
        assert settle(bridge) == {"ok": False, "reason": "discovery-failed"}
        held.refresh_from_db()
    assert held.reveal() == {"api_key": "held"}
    assert f'"Resource {bridge.pk}"' in bridge.last_error and "rename" in bridge.last_error
    assert not bridge.discovery_ready and bridge.credential_id != held.pk


@pytest.mark.parametrize("holder", ["mcp_server", "agent"])
def test_a_derived_credential_something_else_uses_stays_when_its_integration_leaves(bridge, holder):
    with system_context(reason="test a derived credential an agent or server uses"):
        bridge.config = {"derived": True, "derived_name": "Discovered resource key"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived_pk = bridge.credential_id
        user = _hold(bridge.credential, holder, bridge.owner)
        bridge.delete()
        user.refresh_from_db()
        assert Credential.objects.filter(pk=derived_pk).exists()
    assert (user.credential_id if holder == "mcp_server" else user.inference_credential_id) == derived_pk


def test_a_derived_credential_whose_grant_was_lost_leaves_with_its_integration(bridge):
    with system_context(reason="test an integration deleted after its source grant went"):
        bridge.config = {"derived": True, "derived_name": "Discovered resource key"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived_pk, source_pk = bridge.credential_id, bridge.discovery_credential_id
        # The grant removed by a path that skips its disconnect: the integration keeps the material.
        Credential.objects.filter(pk=source_pk).delete()
        bridge.refresh_from_db()
        assert (bridge.discovery_credential_id, bridge.credential_id) == (None, derived_pk)
        bridge.delete()
        assert not Credential.objects.filter(pk=derived_pk).exists()


def test_a_disconnected_integration_waits_for_the_discovery_its_reconnect_runs(bridge, sent_tasks):
    with system_context(reason="test a secret re-entered while disconnected"):
        assert settle(bridge)["ok"]
        bridge.set_lifecycle("disconnected")
        bridge.credential.replace_material({"api_key": "re-entered"})
        bridge.refresh_from_db()
        # Discovery did not run: it waits for the reconnect, which is not a failure.
        assert not bridge.discovery_pending and not bridge.discovery_ready
        with pytest.raises(IntegrationError, match="connected again") as waiting:
            bridge.require_discovery_ready()
        assert waiting.value.transient
        bridge.set_lifecycle("connected")
        assert bridge.discovery_pending
        assert settle(bridge)["ok"] and bridge.discovery_ready


def test_a_derived_credential_whose_grant_was_lost_is_still_derived(bridge, sent_tasks):
    with system_context(reason="test a derived credential after its grant went"):
        bridge.config = {"derived": True, "derived_name": "Shared resource"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived, generation = bridge.credential, bridge.discovery_generation
        # The grant removed by a path that skips its disconnect: the integration keeps the material.
        Credential.objects.filter(pk=bridge.discovery_credential_id).delete()
        bridge.refresh_from_db()
        assert bridge.discovery_credential_id is None and bridge.derived_credential_id == derived.pk
        sent_tasks.clear()
        # Re-entering it is refused as derived material is, not accepted into a discovery that cannot run.
        with pytest.raises(ValidationError, match="derived this credential"):
            derived.replace_material({"api_key": "re-entered"})
        bridge.refresh_from_db()
        assert bridge.discovery_ready and bridge.discovery_generation == generation and sent_tasks == []
        # Another integration's discovery deriving that name overwrites it, as any derived attachment.
        grant = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="Sibling grant", material={"api_key": "sibling"},
        )
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=grant, lifecycle="paused",
            config={"derived": True, "derived_name": "Shared resource"},
        )
        assert settle(sibling)["ok"] and sibling.credential_id == derived.pk


def test_an_integration_deleted_through_a_stale_instance_takes_the_credential_it_derived_since(bridge):
    with system_context(reason="test a delete through an instance read before discovery derived"):
        stale = ConnectionBridge.objects.get(pk=bridge.pk)
        bridge.config = {"derived": True, "derived_name": "Discovered resource key"}
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived_pk = bridge.credential_id
        assert stale.credential_id != derived_pk
        stale.delete()
        assert not Credential.objects.filter(pk=derived_pk).exists()


def test_a_derived_credential_leaves_with_its_integration(bridge, sent_tasks):
    config = {"derived": True, "derived_name": "Discovered resource key"}
    with system_context(reason="test an integration deleted with its derived credential"):
        source, vendor, owner = bridge.credential, bridge.vendor, bridge.owner
        bridge.config = config
        bridge.save(update_fields=["config"])
        assert settle(bridge)["ok"]
        derived_pk = bridge.credential_id
        bridge.delete()
        assert not Credential.objects.filter(pk=derived_pk).exists()
        assert Credential.objects.filter(pk=source.pk).exists()
        # A new integration deriving the same name is not blocked by what the first one left.
        again = ConnectionBridge.objects.create(
            vendor=vendor, owner=owner, credential=source, lifecycle="connected", config=config,
        )
        assert settle(again)["ok"]
        derived = Credential.objects.get(pk=again.credential_id)
    assert derived.name == "Discovered resource key" and again.discovery_credential_id == source.pk


def test_a_derived_credential_another_integration_attaches_stays_when_one_leaves(bridge, sent_tasks):
    with system_context(reason="test one of two integrations deriving one name deleted"):
        sibling = ConnectionBridge.objects.create(
            vendor=bridge.vendor, owner=bridge.owner, credential=bridge.credential, lifecycle="paused",
        )
        for row in (bridge, sibling):
            row.config = {"derived": True, "derived_name": "Shared resource"}
            row.save(update_fields=["config"])
            assert settle(row)["ok"]
        derived_pk = bridge.credential_id
        assert sibling.credential_id == derived_pk
        sibling.delete()
        bridge.refresh_from_db()
        assert Credential.objects.filter(pk=derived_pk).exists()
    assert bridge.credential_id == derived_pk and bridge.discovery_ready


@pytest.mark.parametrize("path", ["replace", "create"])
def test_a_re_entry_that_changes_nothing_keeps_discovery(bridge, sent_tasks, path):
    with system_context(reason="test an unchanged re-entry"):
        credential = Credential.objects.create_local_credential(
            bridge.owner, kind="static_token", name="Same key", material={"api_key": "same"},
        )
        bridge.attach_credential(credential)
        assert settle(bridge)["ok"]
        generation = bridge.discovery_generation
        sent_tasks.clear()
        if path == "replace":
            credential.replace_material({"api_key": "same"})
        else:
            Credential.objects.create_local_credential(
                bridge.owner, kind="static_token", name="Same key", material={"api_key": "same"},
            )
        bridge.refresh_from_db()
    assert bridge.discovery_generation == generation and bridge.discovery_ready
    assert sent_tasks == []


def test_a_secret_re_entered_while_disconnected_is_discovered_on_resume(bridge, sent_tasks):
    operator = create_platform_admin("resume-after-re-entry")
    with system_context(reason="test re-entry while disconnected"):
        assert settle(bridge)["ok"]
        bridge.disconnect()
        generation = bridge.discovery_generation
        sent_tasks.clear()
        bridge.credential.replace_material({"api_key": "re-entered"})
        bridge.refresh_from_db()
        assert bridge.discovery_generation == generation + 1
        assert not bridge.discovery_pending and not bridge.discovery_ready and sent_tasks == []
    with actor_context(operator):
        Integration.objects.get(pk=bridge.pk).resume()
    with system_context(reason="test rediscovery after resume"):
        bridge.refresh_from_db()
        assert bridge.lifecycle == "connected" and bridge.discovery_pending
        assert [name for name, _options in sent_tasks] == [DISCOVERY_TASK]
        assert settle(bridge)["ok"]
    assert bridge.discovery_ready
