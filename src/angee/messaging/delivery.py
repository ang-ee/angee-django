"""One outbound dispatch path; MessageManager owns leases, retries and settlement.

Dispatch recovery is at-most-once after a committed effect claim: an expired
claim never blindly republishes. Native transient integration refusals promise
non-acceptance and can retry. Lost settlements and ambiguous acknowledgements
require an operator decision or a transport that declares safe reconciliation.
Advisory task locks reduce contention; the persisted row fence is authoritative.
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

from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.jobs.enqueue import celery_app, enqueue_task
from angee.jobs.locks import record_lock_key, task_lock
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain
from angee.messaging.constants import DELIVER_MESSAGE_TASK

logger = logging.getLogger(__name__)
DELIVERY_TASK_EXPIRES = 300
MAX_DELIVERY_ATTEMPTS = 4
MAX_RECONCILE_ATTEMPTS = 5
RECONCILE_BACKOFF = timedelta(minutes=1)


def queue_message_delivery(message: Any) -> bool:
    """Dispatch due drafts/queues or eligible uncertain recovery on commit.

    FAILED rows require the explicit operator retry verb. Uncertain rows retain
    their status and revision; recovery only runs for a possibly accepted send.
    """

    if message.pk is None:
        raise ValidationError("Cannot deliver an unsaved message.")
    with system_context(reason="messaging.delivery.queue"), transaction.atomic():
        row = type(message).objects.sudo(reason="messaging.delivery.queue").lock_if_supported().get(pk=message.pk)
        if row.status != row.MessageStatus.UNCERTAIN:
            row.validate_delivery()
        now = timezone.now()
        if (
            row.status not in (row.MessageStatus.DRAFT, row.MessageStatus.QUEUED, row.MessageStatus.UNCERTAIN)
            or row.delivery_blocked or row.delivery_held
            or (row.status == row.MessageStatus.QUEUED and row.delivery_claim is not None)
            or (row.scheduled_at is None and row.delivery_lease_until is not None and row.delivery_lease_until > now)
        ):
            return False
        if row.status == row.MessageStatus.UNCERTAIN:
            if row.delivery_operator_only or not row.delivery_possibly_accepted:
                return False
            if row.scheduled_at is not None and row.scheduled_at > now:
                return False
            if row.delivery_lease_until is not None and row.delivery_lease_until > now:
                return False
            # scheduled_at retains the due recovery horizon until a worker claims
            # it; a lease alongside it fences a still-pending broker submission.
            row.scheduled_at = row.scheduled_at or now
            row.delivery_lease_until = now + timedelta(seconds=DELIVERY_TASK_EXPIRES)
            type(row)._base_manager.filter(pk=row.pk).update(
                scheduled_at=row.scheduled_at, delivery_lease_until=row.delivery_lease_until,
            )
        else:
            row.status = row.MessageStatus.QUEUED
            row.scheduled_at = None
            row.delivery_lease_until = now + timedelta(seconds=DELIVERY_TASK_EXPIRES)
            row.save(update_fields=("status", "scheduled_at", "delivery_lease_until", "updated_at"))
        # The shared prefork worker consumes Celery's configured default queue.
        # Threads-pool queue workers cannot enforce the claim lease's time limits,
        # so an explicit queue overrides any route to them.
        enqueue_task(
            DELIVER_MESSAGE_TASK,
            kwargs={"model_label": row._meta.label_lower, "pk": row.pk, "external_id": row.external_id},
            queue=celery_app.conf.task_default_queue, expires=DELIVERY_TASK_EXPIRES, robust=True,
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
        backend = None
        if message is None:
            uncertain = model.objects.sudo(reason="messaging.delivery.reconcile").filter(
                pk=pk, external_id=external_id, status="uncertain",
            ).first()
            if uncertain is None:
                return {"ok": True, "skipped": True, "reason": "missing-or-stale-or-held"}
            message = model.objects.claim_delivery(pk, token=external_id, reconcile=True)
            if message is None:
                return {"ok": True, "delivered": False, "status": "uncertain"}
        reconciling = message.delivery_needs_reconciliation
        try:
            try:
                backend = message.transport_channel(reason="messaging.delivery.channel").backend
            except Exception as error:
                # Backend construction precedes any possible provider effect.
                refusal = (
                    DeliveryUncertain("The prior delivery still needs reconciliation.") if reconciling
                    else IntegrationError(safe_integration_failure(error).message)
                )
                result = model.objects.record_delivery(
                    pk, token=external_id, claim=message.delivery_claim, error=refusal,
                )
                return {"ok": False, "delivered": False, "status": result}
            with ExitStack() as contexts:
                try:
                    locked = contexts.enter_context(backend.delivery_lock())
                except Exception as error:
                    logger.warning("Delivery lock unavailable (%s).", type(error).__name__)
                    locked = False
                if not locked:
                    result = model.objects.record_delivery(
                        pk, token=external_id, claim=message.delivery_claim,
                        error=(DeliveryUncertain("The prior delivery still needs reconciliation.")
                               if reconciling else None),
                        contention=not reconciling,
                    )
                else:
                    try:
                        outcome = backend.deliver(message)
                        if outcome is False:
                            outcome = DeliveryOutcome(accepted=False)
                        if not isinstance(outcome, DeliveryOutcome):
                            raise TypeError("ChannelBackend.deliver must return DeliveryOutcome.")
                    except Exception as error:
                        result = model.objects.record_delivery(
                            pk, token=external_id, claim=message.delivery_claim, error=error,
                        )
                    else:
                        result = model.objects.record_delivery(
                            pk, token=external_id, claim=message.delivery_claim, outcome=outcome,
                        )
        finally:
            if backend is not None:
                try:
                    backend.close()
                except Exception as error:
                    logger.warning("Closing delivery transport failed (%s).", type(error).__name__)
        return {"ok": result != "failed", "delivered": result == "sent", "status": result}
