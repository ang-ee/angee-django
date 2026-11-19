"""Messaging write behavior."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import actor_context, system_context

import tests.test_messaging  # noqa: F401 -- register the fixture model graph before database setup
from angee.base.signals import mute_changes
from angee.messaging import delivery
from angee.messaging.backends import DeliveryOutcome, ManualChannelBackend
from angee.messaging.testing.models import Message
from tests.test_messaging import channel as channel


@pytest.mark.parametrize("kind", list(Message.MessageKind))
def test_content_edit_validation_uses_retained_kind(
    db: None,
    django_assert_num_queries: Callable[..., AbstractContextManager[Any]],
    kind: str,
) -> None:
    """The edit and delete predicates consume the row's owning kind without extra reads."""
    message = Message(direction=Message.Direction.INTERNAL, message_type=kind)
    with django_assert_num_queries(0):
        assert (message.content_edit_error() is None) == (kind == Message.MessageKind.COMMENT)
        assert (message.delete_error() is None) == (kind == Message.MessageKind.COMMENT)


def test_notification_messages_remain_readonly(db: None) -> None:
    """The retained notification kind, not obsolete payload metadata, owns edit policy."""
    message = Message(direction="internal", message_type="notification")
    assert message.content_edit_error() is not None
    assert message.delete_error() is not None
    assert message.trash_error() is not None


@pytest.mark.django_db(transaction=True)
def test_queued_delivery_waits_for_commit_and_persists_status(
    channel: Any, monkeypatch: pytest.MonkeyPatch
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
    delivered: list[Any] = []
    # The stored channel's own backend sends: delivery reaches it through the channel as stored.
    monkeypatch.setattr(
        ManualChannelBackend, "deliver",
        lambda _backend, message: delivered.append(message.channel_id) or DeliveryOutcome(accepted=True),
    )

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
        monkeypatch.setattr(delivery, "task_lock", lambda _key: nullcontext(True))
        outcome = delivery.run_message_delivery(**payloads[0])
        assert outcome["delivered"] is True
        assert delivered == [channel.pk]
        message.refresh_from_db()
        assert message.status == Message.MessageStatus.SENT
        assert message.sent_at is not None


def test_reserve_delivery_leases_a_due_draft_once_and_publishes_nothing(
    channel: Any, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: Any,
) -> None:
    """The row reserves a due draft under its lock; a live lease refuses a second reservation."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.jobs.enqueue.celery_app.send_task",
        lambda _name, *, kwargs, **_options: sent.append(kwargs),
    )
    with (
        django_capture_on_commit_callbacks(execute=True),
        system_context(reason="messaging reservation setup"),
        mute_changes(),
    ):
        message = Message.objects.create(
            channel_id=channel.pk,
            platform="email",
            direction="outbound",
            status="draft",
            external_id="reserved-delivery",
        )
        row = message.reserve_delivery()
        assert row is not None and row.pk == message.pk
        assert message.status == Message.MessageStatus.QUEUED
        assert message.scheduled_at is None and message.delivery_lease_until is not None
        assert message.delivery_claim is None
        assert message.reserve_delivery() is None
        message.refresh_from_db()
        assert message.status == Message.MessageStatus.QUEUED
    assert sent == []


def test_reserve_delivery_refuses_an_unsaved_or_inbound_message(channel: Any) -> None:
    """Reservation requires the persisted outbound envelope every delivery entrypoint shares."""
    with system_context(reason="messaging reservation refusal"), mute_changes():
        with pytest.raises(ValidationError, match="unsaved message"):
            Message(channel_id=channel.pk, direction="outbound", external_id="unsaved").reserve_delivery()
        inbound = Message.objects.create(
            channel_id=channel.pk, platform="email", direction="inbound", status="draft", external_id="inbound-row",
        )
        with pytest.raises(ValidationError, match="outbound message"):
            inbound.reserve_delivery()
