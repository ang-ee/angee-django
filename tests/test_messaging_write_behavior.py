"""Messaging write behavior."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from django.db import transaction
from rebac import system_context

import tests.test_messaging  # noqa: F401 -- register the fixture model graph before database setup
from angee.graphql.publishing import mute_changes
from angee.messaging import delivery
from tests.messaging_models import Message, TrackingValue
from tests.test_messaging import channel as channel


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("tracked", [False, True])
def test_content_edit_validation_reads_tracking(
    composed_tables: None, monkeypatch: pytest.MonkeyPatch, tracked: bool
) -> None:
    """Content edit validation reads tracking."""
    with system_context(reason="messaging edit validation setup"), mute_changes():
        message = Message.objects.create(direction=Message.Direction.INTERNAL, message_type=Message.MessageKind.COMMENT)
        if tracked:
            TrackingValue.objects.create(message_id=message.pk, field_name="status", field_label="Status")
    with system_context(reason="messaging edit validation"):
        assert message.content_edit_error() == ("Messages with tracking values cannot be edited." if tracked else None)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefetched", [False, True])
def test_content_edit_validation_reuses_prefetched_tracking(
    composed_tables: None,
    django_assert_num_queries: Callable[..., AbstractContextManager[Any]],
    prefetched: bool,
) -> None:
    """Native prefetch avoids another query; uncached validation reads tracking."""
    with system_context(reason="messaging prefetched tracking setup"), mute_changes():
        message = Message.objects.create(
            direction=Message.Direction.INTERNAL,
            message_type=Message.MessageKind.COMMENT,
        )
        if prefetched:
            message = Message.objects.prefetch_related("tracking_values").get(pk=message.pk)
        TrackingValue.objects.create(message_id=message.pk, field_name="status", field_label="Status")
    with system_context(reason="messaging prefetched tracking validation"):
        with django_assert_num_queries(0 if prefetched else 1):
            assert message.content_edit_error() == (
                None if prefetched else "Messages with tracking values cannot be edited."
            )


@pytest.mark.django_db(transaction=True)
def test_queued_delivery_waits_for_commit_and_persists_status(
    channel: Any, composed_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queued delivery waits for commit and persists status."""
    with system_context(reason="messaging delivery setup"), mute_changes():
        message = Message.objects.create(
            channel_id=channel.pk,
            platform="email",
            direction="outbound",
            status="failed",
            external_id="routed-delivery",
        )
    payloads: list[dict[str, Any]] = []
    monkeypatch.setattr(delivery, "enqueue_task", lambda _name, *, kwargs: payloads.append(kwargs))
    channels = Mock()
    channels.sudo.return_value = channels
    channels.get.return_value = SimpleNamespace(backend=SimpleNamespace(deliver=lambda message: True))
    get_model = delivery.apps.get_model

    def delivery_model(app_label: str, model_name: str | None = None) -> Any:
        if (app_label, model_name) == ("messaging", "Channel"):
            return SimpleNamespace(objects=channels)
        return get_model(app_label, model_name)

    monkeypatch.setattr(delivery.apps, "get_model", delivery_model)
    monkeypatch.setattr(delivery, "task_lock", lambda _key: nullcontext(True))
    with system_context(reason="messaging delivery"), mute_changes():
        with transaction.atomic():
            assert delivery.queue_message_delivery(message)
            assert payloads == []
        assert payloads == [
            {"model_label": message._meta.label_lower, "pk": message.pk, "external_id": message.external_id}
        ]
        outcome = delivery.run_message_delivery(**payloads[0])
        assert outcome["delivered"] is True
        channels.get.assert_called_once_with(pk=message.channel_id)
        message.refresh_from_db()
        assert message.status == Message.MessageStatus.SENT
        assert message.sent_at is not None
