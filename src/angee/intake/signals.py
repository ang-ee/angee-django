"""Intake-owned receiver for messaging's generic message-ingested seam."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, system_context

from angee.base.exceptions import DomainError
from angee.decisions.signals import decision_answered
from angee.messaging.events import message_landed

logger = logging.getLogger(__name__)


def connect() -> None:
    """Listen for ingested messages without adding another messaging hook."""

    decision_answered.connect(apply_access_answer, dispatch_uid="intake.apply_access_answer")
    message_landed.connect(
        capture_channel_message,
        dispatch_uid="intake.capture_channel_message.message_landed",
    )


def capture_channel_message(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Resolve the channel and isolate intake capture from primary message ingest.

    Messaging sends this signal synchronously inside its ingest transaction, so
    successful capture remains atomic with the message. Expected domain refusals
    retain primary ingest; programming errors propagate so retries can recover.
    """

    del sender
    channel = kwargs.get("channel")
    for message in kwargs.get("created_batch") or (instance,):
        _capture_channel_message(message, channel=channel)


def _capture_channel_message(instance: Any, *, channel: Any = None) -> None:
    """Capture one admitted channel message within its ingest transaction."""
    if instance.channel_id is None:
        return
    try:
        if channel is None:
            channel = instance.transport_channel(reason="intake.capture.channel")
        if channel is not None:
            channel.capture_ingested_message(instance)
    except (DomainError, ValidationError, PermissionDenied) as error:
        logger.warning(
            "Skipped intake capture for message %s on channel %s: %s",
            instance.pk,
            instance.channel_id,
            error,
        )


def apply_access_answer(sender: Any, decision: Any, **kwargs: Any) -> None:
    """The request owner consumes its current answer through its public write path."""
    if decision.kind != "intake.access":
        return
    need = apps.get_model("intake", "Need")
    with system_context(reason="intake.answer.current_need"):
        rows = list(need.objects.db_manager(decision._state.db).filter(access_decision=decision))
    for row in rows:
        row.with_actor(decision.answered_by).apply_access_answer(decision)
