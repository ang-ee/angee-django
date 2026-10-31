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
from typing import Any

from django.apps import apps
from django.db import router, transaction
from rebac import system_context

from angee.base.locks import record_lock_key, task_lock
from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.jobs.enqueue import celery_app, enqueue_task
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain
from angee.messaging.constants import (
    DELIVER_MESSAGE_TASK,
)
from angee.messaging.constants import (
    DELIVERY_TASK_EXPIRES as DELIVERY_TASK_EXPIRES,
)
from angee.messaging.constants import (
    MAX_DELIVERY_ATTEMPTS as MAX_DELIVERY_ATTEMPTS,
)
from angee.messaging.constants import (
    MAX_RECONCILE_ATTEMPTS as MAX_RECONCILE_ATTEMPTS,
)
from angee.messaging.constants import (
    RECONCILE_BACKOFF as RECONCILE_BACKOFF,
)

logger = logging.getLogger(__name__)


def queue_message_delivery(message: Any) -> bool:
    """Submit the Message owner's reserved identifier envelope to the prefork queue."""
    using = message._state.db or router.db_for_write(type(message), instance=message)
    with transaction.atomic(using=using):
        row = message.reserve_delivery()
        if row is None:
            return False
        enqueue_task(
            DELIVER_MESSAGE_TASK,
            kwargs={"model_label": row._meta.label_lower, "pk": row.pk, "external_id": row.external_id},
            queue=celery_app.conf.task_default_queue, expires=DELIVERY_TASK_EXPIRES, robust=True, using=using,
        )
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
