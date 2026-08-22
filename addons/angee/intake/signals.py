"""Intake-owned receiver for messaging's generic message-ingested seam."""

from __future__ import annotations

import logging
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError

from angee.messaging.events import message_ingested

logger = logging.getLogger(__name__)


def connect() -> None:
    """Listen for ingested messages without adding another messaging hook."""

    message_ingested.connect(
        capture_channel_message,
        dispatch_uid="intake.capture_channel_message.message_ingested",
    )


def capture_channel_message(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Resolve the channel and isolate intake capture from primary message ingest.

    Messaging sends this signal synchronously inside its ingest transaction, so
    successful capture remains atomic with the message. Capture failures must not
    propagate: doing so rolls back the primary message and poisons bridge retries.
    """

    del sender, kwargs
    if instance.channel_id is None:
        return
    try:
        channel_model = apps.get_model("messaging", "Channel")
        apps.get_model("intake", "Need")
    except LookupError:
        # Source-only test graphs may install addon declarations without emitted
        # concrete runtime models. The global messaging seam must remain inert.
        return
    channel = channel_model._base_manager.select_related("intake_queue").filter(pk=instance.channel_id).first()
    if channel is not None:
        try:
            channel.capture_ingested_message(instance)
        except ValidationError as error:
            logger.warning(
                "Skipped intake capture for message %s on channel %s: %s",
                instance.pk,
                channel.pk,
                error,
            )
        except Exception:
            logger.exception(
                "Intake capture failed for message %s on channel %s; primary ingest will continue.",
                instance.pk,
                channel.pk,
            )
