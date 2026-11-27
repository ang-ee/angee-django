"""One outbound dispatch path; the row reserves it, MessageManager owns claims, retries and settlement.

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
from django.db import transaction
from django.views.decorators.debug import sensitive_variables
from rebac import system_context

from angee.base.locks import record_lock_key, task_lock
from angee.integrate.errors import IntegrationError, safe_integration_failure
from angee.jobs.enqueue import celery_app, enqueue_task
from angee.messaging.backends import DeliveryOutcome, DeliveryUncertain
from angee.messaging.constants import DELIVER_MESSAGE_TASK, DELIVERY_TASK_EXPIRES

logger = logging.getLogger(__name__)


def queue_message_delivery(message: Any) -> bool:
    """Submit the row's reserved delivery envelope to the prefork queue on commit.

    ``Message.reserve_delivery`` decides eligibility and stamps the dispatch
    lease; this dispatch only publishes identifiers through ``enqueue_task``.
    """

    with transaction.atomic():
        row = message.reserve_delivery()
        if row is None:
            return False
        # The shared prefork worker consumes Celery's configured default queue.
        # Threads-pool queue workers cannot enforce the claim lease's time limits,
        # so an explicit queue overrides any route to them.
        enqueue_task(
            DELIVER_MESSAGE_TASK,
            kwargs={"model_label": row._meta.label_lower, "pk": row.pk, "external_id": row.external_id},
            queue=celery_app.conf.task_default_queue, expires=DELIVERY_TASK_EXPIRES, robust=True,
        )
    return True


def _record_failed_delivery(model: Any, pk: Any, *, token: str, claim: Any, error: Exception) -> str:
    """Record ``error`` as the delivery's settlement; called outside any handler of it.

    A transport's exception can quote the mail (a server's reply echoing a recipient, a line or a
    link), so it never leaves the delivery task: when recording the settlement fails as well, that
    failure is raised on its own, with no context, and the transport's error is logged by its type
    and its cause's type alone. Without that second failure nothing is logged here.
    """

    try:
        return model.objects.record_delivery(pk, token=token, claim=claim, error=error)
    except Exception as settlement:
        failed = settlement
    kinds = type(error).__name__ + (f" ({type(error.__cause__).__name__})" if error.__cause__ is not None else "")
    logger.error(
        "Recording a failed delivery failed (%s); the delivery had failed with %s.", type(failed).__name__, kinds,
    )
    raise failed from None


# The worker's path to the transport holds the message, its parts and the built email, the
# transport library's own frames included: an error report (DEBUG off) hides every local from here down.
@sensitive_variables()
def run_message_delivery(model_label: str, pk: Any, external_id: str) -> dict[str, Any]:
    """Publish under the message lock and the transport's declared delivery lock.

    The transport channel's integration admits the send first
    (``Integration.require_discovery_ready``): while its connection discovery is
    pending the delivery is deferred as lock contention is, spending no attempt,
    and a discovery that failed fails it with the discovery's own message. A
    channel that rediscovers may hold a grant its discovery has not confirmed.
    """

    model = apps.get_model(model_label)
    lock_key = record_lock_key(model_label, pk, "deliver")
    with system_context(reason="messaging.delivery.run"), task_lock(lock_key) as acquired:
        if not acquired:
            return {"ok": True, "skipped": True, "reason": "delivery-already-running"}
        message = model.objects.claim_delivery(pk, token=external_id)
        backend: Any = None
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
            refusal: Exception | None = None
            try:
                channel = message.transport_channel(reason="messaging.delivery.channel")
                backend = channel.backend
            except Exception as error:
                # Backend construction precedes any possible provider effect.
                refusal = (
                    DeliveryUncertain("The prior delivery still needs reconciliation.") if reconciling
                    else IntegrationError(safe_integration_failure(error).message)
                )
            if refusal is not None:
                result = _record_failed_delivery(model, pk, token=external_id, claim=message.delivery_claim,
                                                 error=refusal)
                return {"ok": False, "delivered": False, "status": result}
            unready: IntegrationError | None = None
            try:
                channel.require_discovery_ready()
            except IntegrationError as refusal:
                unready = refusal
            with ExitStack() as contexts:
                try:
                    # A pending discovery holds the send back as a held delivery lock does.
                    locked = unready is None and contexts.enter_context(backend.delivery_lock())
                except Exception as error:
                    logger.warning("Delivery lock unavailable (%s).", type(error).__name__)
                    locked = False
                if locked:
                    try:
                        # Asked again under the lock on the channel as stored now: a secret
                        # re-entered while the send waited for the lock holds it back.
                        channel.require_stored_discovery_ready()
                    except IntegrationError as refusal:
                        unready, locked = refusal, False
                if unready is not None and not unready.transient:
                    result = model.objects.record_delivery(
                        pk, token=external_id, claim=message.delivery_claim, error=unready,
                    )
                elif not locked:
                    result = model.objects.record_delivery(
                        pk, token=external_id, claim=message.delivery_claim,
                        error=(DeliveryUncertain("The prior delivery still needs reconciliation.")
                               if reconciling else None),
                        contention=not reconciling,
                    )
                else:
                    failure: Exception | None = None
                    try:
                        outcome = backend.deliver(message)
                        if outcome is False:
                            outcome = DeliveryOutcome(accepted=False)
                        if not isinstance(outcome, DeliveryOutcome):
                            raise TypeError("ChannelBackend.deliver must return DeliveryOutcome.")
                    except Exception as error:
                        failure = error
                    if failure is not None:
                        result = _record_failed_delivery(model, pk, token=external_id, claim=message.delivery_claim,
                                                         error=failure)
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
