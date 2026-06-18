"""Tests for integrate webhook subscription and delivery behavior."""

from __future__ import annotations

import hmac
import socket
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from rebac import system_context, to_object_ref, to_subject_ref
from rebac.models import active_relationship_model

from angee.integrate.events import EventKind
from angee.integrate.models import Bridge, IntegrationMixin
from angee.integrate.net import validate_public_url
from angee.integrate.webhooks import HTTP_TIMEOUT_SECONDS, SIGNATURE_HEADER
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    WebhookSubscription,
    _create_missing_tables,
    make_integration,
)


class DispatchBridge(Bridge):
    """Concrete bridge fixture used only for inbound dispatch tests."""

    class Meta(Bridge.Meta):
        """Django model options for the inbound dispatch bridge fixture."""

        abstract = False
        app_label = "tests"
        db_table = "test_integrate_webhook_dispatch_bridge"
        rebac_resource_type = "tests/webhook_dispatch_bridge"
        rebac_id_attr = "sqid"

    def sync(self) -> None:
        """No-op sync implementation for the fixture."""

    def handle_webhook(self, payload: Any) -> None:
        """Record that the verified payload was handled."""

        self.calls.append(("handle", payload))

    def verify_webhook(self, request: Any) -> bool:
        """Record verification and return the fixture's configured result."""

        self.calls.append(("verify", request))
        if hasattr(self, "verify_error"):
            raise self.verify_error
        return self.accepts

    def start_live(self) -> None:
        """No-op live subscription start for the fixture."""

    def stop_live(self) -> None:
        """No-op live subscription stop for the fixture."""


class FakeResponse:
    """Response double for pinned HTTP connection calls."""

    def __init__(self, status: int) -> None:
        """Store the HTTP status exposed by http.client responses."""

        self.status = status


@pytest.fixture()
def webhook_tables(transactional_db: Any) -> Iterator[None]:
    """Create IAM and webhook tables required by source-addon webhook tests."""

    del transactional_db
    created_iam_models = _create_missing_tables(IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS)
    webhook_created = False
    if WebhookSubscription._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(WebhookSubscription)
        webhook_created = True

    try:
        yield
    finally:
        if webhook_created:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(WebhookSubscription)
        if created_iam_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_iam_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_webhook_subscription_owner_is_field_backed(webhook_tables: None) -> None:
    """WebhookSubscription owner access comes from the owner field."""

    del webhook_tables
    call_command("rebac", "sync", verbosity=0)
    user = get_user_model().objects.create_user(username="webhook-owner", email="owner@example.com")
    other_user = get_user_model().objects.create_user(
        username="webhook-other",
        email="other@example.com",
    )

    with system_context(reason="test webhook owner grant"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks.example.test/events",
            secret="owner-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
        )

    assert not _owner_tuple_exists(user, subscription)
    assert WebhookSubscription.objects.with_actor(user).filter(pk=subscription.pk).exists()
    assert not WebhookSubscription.objects.with_actor(other_user).filter(pk=subscription.pk).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "url",
    [
        "file:///private/hook",
        "http://127.0.0.1/hook",
        "http://10.0.0.1/hook",
        "http://172.16.0.1/hook",
        "http://192.168.0.1/hook",
        "http://169.254.1.1/hook",
        "http://169.254.169.254/hook",
        "http://[::1]/hook",
        "http://[fd00::1]/hook",
        "http://[fd00:ec2::254]/hook",
    ],
)
def test_validate_public_url_rejects_unsafe_targets(url: str) -> None:
    """The webhook URL validator rejects schemes and non-public addresses."""

    with pytest.raises(ValidationError):
        validate_public_url(url)


@pytest.mark.django_db(transaction=True)
def test_validate_public_url_accepts_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname resolving only to a public address is accepted."""

    _resolve_to(monkeypatch, "93.184.216.34")

    validate_public_url("https://hooks.example.test/events")


def test_runtime_marker_is_per_class() -> None:
    """Pure abstract bases stay unmaterialized; subclasses opt in per class."""

    class RuntimeBridge(Bridge):
        runtime = True

        class Meta(Bridge.Meta):
            abstract = True
            app_label = "tests"

    assert IntegrationMixin.is_runtime_model() is False
    assert Bridge.is_runtime_model() is False
    assert RuntimeBridge.is_runtime_model() is True


@pytest.mark.django_db(transaction=True)
def test_deliver_event_signs_and_posts_only_matching_subscriptions(
    webhook_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Outbound delivery signs raw JSON and skips disabled or filtered subscriptions."""

    del webhook_tables
    call_command("rebac", "sync", verbosity=0)
    _resolve_to(monkeypatch, "93.184.216.34")
    connections = _record_connections(monkeypatch, status=202)

    user = get_user_model().objects.create_user(username="webhook-delivery", email="delivery@example.com")
    conn = make_integration("delivery-account")
    other_conn = make_integration("delivery-other")
    payload = {"bridge": "br_1"}
    body = b'{"bridge":"br_1"}'

    with system_context(reason="test webhook delivery setup"):
        first = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-a.example.test/events",
            secret="first-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
            impl_app_filter=["notes"],
            integration_filter=conn,
            consecutive_failures=3,
        )
        match_all = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-all.example.test/events",
            secret="all-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
        )
        disabled = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-disabled.example.test/events",
            secret="disabled-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
            enabled=False,
        )
        wrong_kind = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-kind.example.test/events",
            secret="kind-secret",
            event_kinds=[EventKind.ACCOUNT_REVOKED.value],
        )
        wrong_impl = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-impl.example.test/events",
            secret="impl-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
            impl_app_filter=["billing"],
        )
        wrong_account = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-account.example.test/events",
            secret="account-secret",
            event_kinds=[EventKind.BRIDGE_SYNCED.value],
            integration_filter=other_conn,
        )

    result = WebhookSubscription.objects.deliver_event(
        kind=EventKind.BRIDGE_SYNCED,
        payload=payload,
        impl_app="notes",
        integration=conn,
    )

    assert result == {"delivered": 2, "errors": 0}
    assert [connection.host for connection in connections] == [
        "hooks-a.example.test",
        "hooks-all.example.test",
    ]
    assert [connection.port for connection in connections] == [443, 443]
    assert [connection.timeout for connection in connections] == [HTTP_TIMEOUT_SECONDS, HTTP_TIMEOUT_SECONDS]
    assert [connection.pinned_address.address for connection in connections] == [
        "93.184.216.34",
        "93.184.216.34",
    ]
    requests = [connection.requests[0] for connection in connections]
    assert [request["url"] for request in requests] == ["/events", "/events"]
    assert {request["method"] for request in requests} == {"POST"}
    assert {request["body"] for request in requests} == {body}
    assert [request["headers"]["Host"] for request in requests] == [
        "hooks-a.example.test",
        "hooks-all.example.test",
    ]
    assert {request["headers"][SIGNATURE_HEADER] for request in requests} == {
        _signature("first-secret", body),
        _signature("all-secret", body),
    }
    assert {connection.closed for connection in connections} == {True}

    first.refresh_from_db()
    match_all.refresh_from_db()
    for delivered in (first, match_all):
        assert delivered.last_delivery_status == "202"
        assert delivered.last_delivery_at is not None
        assert delivered.last_error == ""
        assert delivered.consecutive_failures == 0

    for skipped in (disabled, wrong_kind, wrong_impl, wrong_account):
        skipped.refresh_from_db()
        assert skipped.last_delivery_at is None


@pytest.mark.django_db(transaction=True)
def test_deliver_event_failure_increments_consecutive_failures(
    webhook_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed POST records telemetry and increments the failure counter."""

    del webhook_tables
    call_command("rebac", "sync", verbosity=0)
    _resolve_to(monkeypatch, "93.184.216.34")
    connections = _record_connections(monkeypatch, request_error=ConnectionRefusedError("connection refused"))

    user = get_user_model().objects.create_user(username="webhook-failure", email="failure@example.com")
    with system_context(reason="test webhook failure setup"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-failure.example.test/events",
            secret="failure-secret",
            event_kinds=[EventKind.BRIDGE_ERRORED.value],
            consecutive_failures=4,
        )

    result = WebhookSubscription.objects.deliver_event(
        kind=EventKind.BRIDGE_ERRORED,
        payload={"bridge": "br_1"},
    )

    subscription.refresh_from_db()
    assert result == {"delivered": 0, "errors": 1}
    assert len(connections) == 1
    assert connections[0].closed is True
    assert subscription.consecutive_failures == 5
    assert subscription.last_delivery_at is not None
    assert subscription.last_delivery_status == ""
    assert "ConnectionRefusedError" in subscription.last_error


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
    ],
)
def test_deliver_event_rejects_unsafe_resolved_target_without_connecting(
    webhook_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    """Delivery fails closed when DNS resolves a webhook target to an unsafe address."""

    del webhook_tables
    call_command("rebac", "sync", verbosity=0)
    _resolve_to(monkeypatch, address)
    connections = _record_connections(monkeypatch, status=202)

    user = get_user_model().objects.create_user(
        username=f"webhook-ssrf-{address.replace('.', '-')}",
        email="ssrf@example.com",
    )
    with system_context(reason="test webhook ssrf setup"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-ssrf.example.test/events",
            secret="ssrf-secret",
            event_kinds=[EventKind.BRIDGE_ERRORED.value],
        )

    result = WebhookSubscription.objects.deliver_event(
        kind=EventKind.BRIDGE_ERRORED,
        payload={"bridge": "br_1"},
    )

    subscription.refresh_from_db()
    assert result == {"delivered": 0, "errors": 1}
    assert connections == []
    assert subscription.consecutive_failures == 1
    assert subscription.last_delivery_status == ""
    assert "Webhook URL host must resolve only to public IP addresses." in subscription.last_error


@pytest.mark.django_db(transaction=True)
def test_deliver_event_redirect_response_fails_without_following(
    webhook_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 3xx delivery response is telemetry failure and never another request."""

    del webhook_tables
    call_command("rebac", "sync", verbosity=0)
    _resolve_to(monkeypatch, "93.184.216.34")
    connections = _record_connections(monkeypatch, status=302)

    user = get_user_model().objects.create_user(username="webhook-redirect", email="redirect@example.com")
    with system_context(reason="test webhook redirect setup"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-redirect.example.test/events",
            secret="redirect-secret",
            event_kinds=[EventKind.BRIDGE_ERRORED.value],
            consecutive_failures=2,
        )

    result = WebhookSubscription.objects.deliver_event(
        kind=EventKind.BRIDGE_ERRORED,
        payload={"bridge": "br_1"},
    )

    subscription.refresh_from_db()
    assert result == {"delivered": 0, "errors": 1}
    assert len(connections) == 1
    assert len(connections[0].requests) == 1
    assert connections[0].host == "hooks-redirect.example.test"
    assert subscription.consecutive_failures == 3
    assert subscription.last_delivery_status == "302"
    assert "HTTP 302" in subscription.last_error


@pytest.mark.django_db(transaction=True)
def test_dispatch_inbound_verifies_then_handles_fixture_bridge() -> None:
    """Inbound dispatch calls verify_webhook before handle_webhook."""

    payload = {"event": "vendor.changed"}
    bridge = DispatchBridge()
    bridge.calls = []
    bridge.accepts = True

    result = bridge.dispatch_inbound(payload)

    assert result is True
    assert bridge.calls == [("verify", payload), ("handle", payload)]


@pytest.mark.django_db(transaction=True)
def test_dispatch_inbound_rejects_without_handling_when_verify_returns_false() -> None:
    """Inbound dispatch does not handle a payload rejected by verify_webhook."""

    payload = {"event": "vendor.changed"}
    bridge = DispatchBridge()
    bridge.calls = []
    bridge.accepts = False

    result = bridge.dispatch_inbound(payload)

    assert result is False
    assert bridge.calls == [("verify", payload)]


@pytest.mark.django_db(transaction=True)
def test_dispatch_inbound_does_not_handle_when_verify_raises() -> None:
    """Inbound dispatch does not handle a payload when verification raises."""

    payload = {"event": "vendor.changed"}
    bridge = DispatchBridge()
    bridge.calls = []
    bridge.accepts = True
    bridge.verify_error = RuntimeError("bad signature")

    with pytest.raises(RuntimeError, match="bad signature"):
        bridge.dispatch_inbound(payload)

    assert bridge.calls == [("verify", payload)]


def _record_connections(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: int = 202,
    request_error: Exception | None = None,
) -> list[Any]:
    """Record pinned HTTP(S) connection calls without opening sockets."""

    connections: list[Any] = []

    class RecordingConnection:
        """Connection double that records request details."""

        def __init__(
            self,
            host: str,
            *,
            port: int,
            timeout: int,
            pinned_address: Any,
            **kwargs: Any,
        ) -> None:
            """Record construction arguments from the pinned connection factory."""

            del kwargs
            self.host = host
            self.port = port
            self.timeout = timeout
            self.pinned_address = pinned_address
            self.requests: list[dict[str, Any]] = []
            self.closed = False
            connections.append(self)

        def request(self, method: str, url: str, *, body: bytes, headers: dict[str, str]) -> None:
            """Record one outbound request or raise the configured transport error."""

            if request_error is not None:
                raise request_error
            self.requests.append(
                {
                    "method": method,
                    "url": url,
                    "body": body,
                    "headers": headers,
                }
            )

        def getresponse(self) -> FakeResponse:
            """Return the configured response status."""

            return FakeResponse(status)

        def close(self) -> None:
            """Record that delivery closed the connection."""

            self.closed = True

    monkeypatch.setattr("angee.integrate.webhooks._PinnedHTTPConnection", RecordingConnection)
    monkeypatch.setattr("angee.integrate.webhooks._PinnedHTTPSConnection", RecordingConnection)
    return connections


def _resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Make DNS resolution return one address for every hostname."""

    def fake_getaddrinfo(hostname: str, port: int | None, *, type: int) -> list[Any]:
        del hostname, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _signature(secret: str, body: bytes) -> str:
    """Return the expected Angee webhook signature for a test request body."""

    return f"sha256={hmac.new(secret.encode(), body, 'sha256').hexdigest()}"


def _owner_tuple_exists(owner: Any, resource: Any) -> bool:
    """Return whether ``owner`` has the stored owner relation on ``resource``."""

    owner_ref = to_subject_ref(owner)
    resource_ref = to_object_ref(resource)
    return (
        active_relationship_model()
        .objects.filter(
            resource_type=resource_ref.resource_type,
            resource_id=resource_ref.resource_id,
            relation="owner",
            subject_type=owner_ref.subject_type,
            subject_id=owner_ref.subject_id,
            optional_subject_relation=owner_ref.optional_relation,
        )
        .exists()
    )
