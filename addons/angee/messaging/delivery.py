"""Durable, idempotent outbound message delivery use-cases."""

from __future__ import annotations

import logging
from typing import Any

from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import record_lock_key, task_lock
from anymail.exceptions import AnymailAPIError
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import system_context
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import Timeout as RequestsTimeout

logger = logging.getLogger(__name__)

DELIVER_MESSAGE_TASK = "messaging.deliver_message"
_NETWORK_ERRORS = (ConnectionError, TimeoutError, RequestsConnectionError, RequestsTimeout)


class TransientDeliveryError(Exception):
    """A network failure or ESP 5xx response that Celery may retry."""


def queue_message_delivery(message: Any) -> bool:
    """Persist ``queued`` and enqueue one delivery task; return whether queued.

    ``external_id`` is the stable delivery token. A terminal ``sent`` row is a
    no-op; every other state can be re-enqueued to repair a lost broker publish
    or explicitly retry a failed attempt.
    """

    if message.pk is None:
        raise ValidationError("Cannot deliver an unsaved message.")
    with system_context(reason="messaging.delivery.queue"), transaction.atomic():
        row = type(message).objects.sudo(reason="messaging.delivery.queue").lock_if_supported().get(pk=message.pk)
        _validate_outbound(row)
        if str(row.status) == str(row.MessageStatus.SENT):
            message.status = row.status
            return False
        row.status = row.MessageStatus.QUEUED
        row.save(update_fields=("status", "updated_at"))
        model_label = row._meta.label_lower
        pk = row.pk
        external_id = str(row.external_id)
        transaction.on_commit(
            lambda: enqueue_task(
                DELIVER_MESSAGE_TASK,
                kwargs={"model_label": model_label, "pk": pk, "external_id": external_id},
            )
        )
    message.status = message.MessageStatus.QUEUED
    return True


def run_message_delivery(model_label: str, pk: Any, external_id: str) -> dict[str, Any]:
    """Run one at-least-once delivery attempt under the shared record lock.

    Lost acknowledgements can transmit twice; dedup is receiver-side best effort
    through the stable Message-ID, not a universal ESP guarantee. Cross-process
    mutual exclusion requires Postgres advisory locks; SQLite/local locking can
    double-send.
    """

    model = apps.get_model(model_label)
    with (
        system_context(reason="messaging.delivery.run"),
        task_lock(record_lock_key(model_label, pk, "deliver")) as acquired,
    ):
        if not acquired:
            return {"ok": True, "skipped": True, "reason": "delivery-already-running"}
        message = _claim(model, pk, external_id)
        if message is None:
            return {"ok": True, "skipped": True, "reason": "missing-or-stale"}
        if str(message.status) == str(message.MessageStatus.SENT):
            return {"ok": True, "skipped": True, "reason": "already-sent"}
        try:
            channel = (
                apps.get_model("messaging", "Channel")
                .objects.sudo(reason="messaging.delivery.channel")
                .get(pk=message.channel_id)
            )
            delivered = channel.backend.deliver(message)
        except Exception as error:
            _record_status(model, pk, external_id, status="failed")
            if _is_transient(error):
                logger.exception("Transient outbound delivery failure for %s:%s.", model_label, pk)
                raise TransientDeliveryError(
                    f"Transient outbound delivery failure for {model_label}:{pk}."
                ) from error
            logger.exception("Permanent outbound delivery failure for %s:%s.", model_label, pk)
            return {
                "ok": False,
                "delivered": False,
                "external_id": external_id,
                "error": type(error).__name__,
            }
        if not delivered:
            _record_status(model, pk, external_id, status="failed")
            return {"ok": False, "delivered": False, "external_id": external_id}
        sent_at = _record_status(model, pk, external_id, status="sent")
        return {
            "ok": True,
            "delivered": True,
            "external_id": external_id,
            "sent_at": sent_at.isoformat() if sent_at is not None else None,
        }


def _claim(model: Any, pk: Any, external_id: str) -> Any | None:
    with system_context(reason="messaging.delivery.claim"), transaction.atomic():
        message = (
            model.objects.sudo(reason="messaging.delivery.claim")
            .lock_if_supported()
            .filter(pk=pk, external_id=external_id)
            .select_related("sender", "thread")
            .first()
        )
        if message is None:
            return None
        _validate_outbound(message)
        if str(message.status) != str(message.MessageStatus.SENT):
            message.status = message.MessageStatus.QUEUED
            message.save(update_fields=("status", "updated_at"))
        return message


def _record_status(model: Any, pk: Any, external_id: str, *, status: str) -> Any | None:
    with system_context(reason=f"messaging.delivery.{status}"), transaction.atomic():
        message = (
            model.objects.sudo(reason=f"messaging.delivery.{status}")
            .lock_if_supported()
            .filter(pk=pk, external_id=external_id)
            .first()
        )
        if message is None or str(message.status) == str(message.MessageStatus.SENT):
            return getattr(message, "sent_at", None)
        message.status = status
        update_fields = ["status", "updated_at"]
        if status == str(message.MessageStatus.SENT) and message.sent_at is None:
            message.sent_at = timezone.now()
            update_fields.append("sent_at")
        message.save(update_fields=tuple(update_fields))
        return message.sent_at


def _validate_outbound(message: Any) -> None:
    errors: dict[str, str] = {}
    if str(message.direction) != str(message.Direction.OUTBOUND):
        errors["direction"] = "Only outbound messages can be delivered."
    if message.channel_id is None:
        errors["channel"] = "Outbound delivery requires a channel."
    if not str(message.external_id or "").strip():
        errors["external_id"] = "Outbound delivery requires a stable external_id."
    if errors:
        raise ValidationError(errors)


def _is_transient(error: Exception) -> bool:
    """Return whether ``error`` is a retryable network or ESP-server failure."""

    if isinstance(error, AnymailAPIError):
        status_code = getattr(error, "status_code", None)
        if isinstance(status_code, int):
            return 500 <= status_code <= 599
    return isinstance(error, _NETWORK_ERRORS)
