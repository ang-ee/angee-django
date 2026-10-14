"""Messaging write behavior."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import transaction
from rebac import actor_context, system_context

import tests.test_messaging  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.publishing import mute_changes
from angee.messaging import delivery
from angee.messaging.backends import DeliveryOutcome
from angee.messaging.testing.models import Message
from tests.test_messaging import channel as channel


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("kind", list(Message.MessageKind))
def test_content_edit_validation_uses_retained_kind(
    composed_tables: None,
    django_assert_num_queries: Callable[..., AbstractContextManager[Any]],
    kind: str,
) -> None:
    """The edit and delete predicates consume the row's owning kind without extra reads."""
    message = Message(direction=Message.Direction.INTERNAL, message_type=kind)
    with django_assert_num_queries(0):
        assert (message.content_edit_error() is None) == (kind == Message.MessageKind.COMMENT)
        assert (message.delete_error() is None) == (kind == Message.MessageKind.COMMENT)


@pytest.mark.django_db(transaction=True)
def test_legacy_notification_payload_remains_readonly(composed_tables: None) -> None:
    """Retired field-tracking messages use the same retained-notification policy."""
    message = Message(direction="internal", message_type="notification", metadata={
        "legacy_tracking": [{"field_name": "status", "old_value": "open", "new_value": "closed"}],
    })
    assert message.content_edit_error() is not None
    assert message.delete_error() is not None
    assert message.trash_error() is not None


@pytest.mark.django_db(transaction=True)
def test_queued_delivery_waits_for_commit_and_persists_status(
    channel: Any, composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicit retry admits FAILED delivery and dispatches only after commit."""
    with system_context(reason="messaging delivery setup"), mute_changes():
        message = Message.objects.create(
            channel_id=channel.pk,
            platform="email",
            direction="outbound",
            status="failed",
            external_id="routed-delivery",
        )
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.jobs.enqueue.celery_app.send_task",
        lambda _name, *, kwargs, **_options: payloads.append(kwargs),
    )
    channels = Mock()
    channels.sudo.return_value = channels
    channels.get.return_value = SimpleNamespace(backend=SimpleNamespace(
        deliver=lambda message: DeliveryOutcome(accepted=True),
        delivery_lock=lambda: nullcontext(True),
        close=lambda: None,
    ))
    get_model = delivery.apps.get_model

    def delivery_model(app_label: str, model_name: str | None = None) -> Any:
        if (app_label, model_name) == ("messaging", "Channel"):
            return SimpleNamespace(objects=channels)
        return get_model(app_label, model_name)

    with system_context(reason="messaging delivery"), mute_changes():
        revision = message.revision
        assert not delivery.queue_message_delivery(message)
        message.refresh_from_db()
        assert message.status == Message.MessageStatus.FAILED and message.revision == revision
        assert message.delivery_claim is None and message.delivery_lease_until is None
        assert payloads == []
        with transaction.atomic(), actor_context(channel.owner):
            message = message.retry_delivery(actor=channel.owner, expected_revision=revision)
            assert message.status == Message.MessageStatus.QUEUED
            assert message.acknowledged_by_id == channel.owner_id
            assert message.local_metadata["delivery_attempts"] == 0
            assert payloads == []
        assert payloads == [
            {"model_label": message._meta.label_lower, "pk": message.pk, "external_id": message.external_id}
        ]
        monkeypatch.setattr(delivery.apps, "get_model", delivery_model)
        monkeypatch.setattr(delivery, "task_lock", lambda _key: nullcontext(True))
        outcome = delivery.run_message_delivery(**payloads[0])
        assert outcome["delivered"] is True
        channels.get.assert_called_once_with(pk=message.channel_id)
        message.refresh_from_db()
        assert message.status == Message.MessageStatus.SENT
        assert message.sent_at is not None
