"""Tests for integrate webhook subscription and delivery behavior."""

from __future__ import annotations

import hmac
import socket
from typing import Any

import httpx2
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rebac import system_context

from angee.integrate.http import HttpClient
from angee.integrate.models import Bridge
from angee.integrate.net import validate_public_url
from tests.conftest import (
    WebhookSubscription,
)


@pytest.mark.django_db(transaction=True)
def test_webhook_subscription_owner_is_field_backed(transactional_db: None) -> None:
    """WebhookSubscription owner access comes from the owner field and moves by transfer."""

    del transactional_db
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
        )

    assert subscription.owner_id == user.pk
    assert WebhookSubscription.objects.with_actor(user).filter(pk=subscription.pk).exists()
    assert not WebhookSubscription.objects.with_actor(other_user).filter(pk=subscription.pk).exists()

    subscription.with_actor(user).transfer_ownership(other_user)
    assert not WebhookSubscription.objects.with_actor(user).filter(pk=subscription.pk).exists()
    assert WebhookSubscription.objects.with_actor(other_user).filter(pk=subscription.pk).exists()


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

    assert Bridge.__dict__.get("runtime", False) is False
    assert RuntimeBridge.__dict__["runtime"] is True


@pytest.mark.django_db(transaction=True)
def test_record_delivery_failure_refreshes_incremented_counter(transactional_db: None) -> None:
    """Failure telemetry leaves the row instance with the database counter value."""

    del transactional_db
    call_command("rebac", "sync", verbosity=0)

    user = get_user_model().objects.create_user(username="webhook-refresh", email="refresh@example.com")
    with system_context(reason="test webhook refresh setup"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks-refresh.example.test/events",
            secret="refresh-secret",
            consecutive_failures=4,
        )
        subscription.record_delivery_failure(status="", error="ConnectionRefusedError: connection refused")

    assert subscription.consecutive_failures == 5


@pytest.mark.django_db(transaction=True)
def test_subscription_test_delivery_signs_and_records_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The retained test action signs its body and persists delivery telemetry."""

    user = get_user_model().objects.create_user(username="webhook-test-delivery")
    with system_context(reason="test webhook delivery setup"):
        subscription = WebhookSubscription.objects.create(
            owner=user,
            target_url="https://hooks.example.test/events",
            secret="delivery-secret",
        )
    sent: list[tuple[str, bytes, dict[str, str]]] = []

    def post(_client: HttpClient, url: str, *, body: bytes, headers: dict[str, str]) -> httpx2.Response:
        sent.append((url, body, headers))
        return httpx2.Response(204)

    monkeypatch.setattr(HttpClient, "post", post)
    with system_context(reason="test webhook delivery"):
        assert subscription.deliver_test() == (True, "Delivered (status 204).")
    url, body, headers = sent[0]
    assert url == subscription.target_url
    assert headers["X-Angee-Signature"] == f"sha256={hmac.new(b'delivery-secret', body, 'sha256').hexdigest()}"
    assert subscription.last_delivery_status == "204"
    assert subscription.consecutive_failures == 0


def _resolve_to(monkeypatch: pytest.MonkeyPatch, address: str) -> None:
    """Make DNS resolution return one address for every hostname."""

    def fake_getaddrinfo(hostname: str, port: int | None, *, type: int) -> list[Any]:
        del hostname, type
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (address, port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
