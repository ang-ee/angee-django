"""One outbound dispatch path; MessageManager owns leases, retries and settlement.

Only a backend's typed transient refusal is retryable. Backends must reconcile
ambiguous publishes before returning; the dispatcher never guesses from SDK
exceptions. Broker loss is recovered only for rows carrying our enqueue lease.
"""

from __future__ import annotations

import logging
from contextlib import ExitStack
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate.errors import IntegrationError
from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import record_lock_key, task_lock
from angee.messaging.backends import DeliveryOutcome
from angee.messaging.constants import DELIVER_MESSAGE_TASK

logger = logging.getLogger(__name__)
DELIVERY_TASK_EXPIRES = 300
MAX_DELIVERY_ATTEMPTS = 4


class TransientDeliveryError(IntegrationError):
    """A definite non-acceptance; None selects the delivery owner's backoff."""

    transient = True

    def __init__(
        self, message: str = "Delivery temporarily unavailable.", *, retry_after: timedelta | None = None,
    ):
        super().__init__(message, transient=True, retry_after=retry_after)


def queue_message_delivery(message: Any) -> bool:
    """Enqueue a due row on commit and establish its lost-publish lease."""

    if message.pk is None:
        raise ValidationError("Cannot deliver an unsaved message.")
    with system_context(reason="messaging.delivery.queue"), transaction.atomic():
        row = type(message).objects.sudo(reason="messaging.delivery.queue").lock_if_supported().get(pk=message.pk)
        row.validate_delivery()
        now = timezone.now()
        if (
            row.status == row.MessageStatus.SENT or row.delivery_blocked or row.delivery_held
            or (row.scheduled_at is None and row.delivery_lease_until is not None and row.delivery_lease_until > now)
        ):
            return False
        row.status = row.MessageStatus.QUEUED
        row.scheduled_at = None
        row.delivery_lease_until = now + timedelta(seconds=DELIVERY_TASK_EXPIRES)
        row.save(update_fields=("status", "scheduled_at", "delivery_lease_until", "updated_at"))
        enqueue_task(
            DELIVER_MESSAGE_TASK,
            kwargs={"model_label": row._meta.label_lower, "pk": row.pk, "external_id": row.external_id},
            expires=DELIVERY_TASK_EXPIRES, robust=True,
        )
    message.status = row.status
    message.scheduled_at = row.scheduled_at
    message.delivery_lease_until = row.delivery_lease_until
    message.revision = row.revision
    return True


def run_message_delivery(model_label: str, pk: Any, external_id: str) -> dict[str, Any]:
    """Publish under the message lock and the transport's declared delivery lock."""

    model = apps.get_model(model_label)
    lock_key = record_lock_key(model_label, pk, "deliver")
    with system_context(reason="messaging.delivery.run"), task_lock(lock_key) as acquired:
        if not acquired:
            return {"ok": True, "skipped": True, "reason": "delivery-already-running"}
        message = model.objects.claim_delivery(pk, token=external_id)
        if message is None:
            return {"ok": True, "skipped": True, "reason": "missing-or-stale-or-held"}
        backend = None
        try:
            try:
                backend = message.transport_channel(reason="messaging.delivery.channel").backend
            except Exception as error:
                result = model.objects.record_delivery(pk, token=external_id, error=error)
                return {"ok": False, "delivered": False, "status": result}
            with ExitStack() as contexts:
                try:
                    locked = contexts.enter_context(backend.delivery_lock())
                except Exception as error:
                    logger.warning("Delivery lock unavailable (%s).", type(error).__name__)
                    locked = False
                if not locked:
                    result = model.objects.record_delivery(pk, token=external_id, contention=True)
                else:
                    try:
                        outcome = backend.deliver(message)
                        if not isinstance(outcome, DeliveryOutcome):
                            raise TypeError("ChannelBackend.deliver must return DeliveryOutcome.")
                    except Exception as error:
                        result = model.objects.record_delivery(pk, token=external_id, error=error)
                    else:
                        result = model.objects.record_delivery(pk, token=external_id, outcome=outcome)
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception as error:
                    logger.warning("Closing delivery transport failed (%s).", type(error).__name__)
        return {"ok": result != "failed", "delivered": result == "sent", "status": result}
