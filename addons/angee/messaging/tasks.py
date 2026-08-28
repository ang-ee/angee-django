"""Celery task wrappers for outbound messaging delivery."""

from __future__ import annotations

from typing import Any

from celery import shared_task

from angee.messaging.delivery import (
    DELIVER_MESSAGE_TASK,
    TransientDeliveryError,
    run_message_delivery,
)


@shared_task(
    name=DELIVER_MESSAGE_TASK,
    autoretry_for=(TransientDeliveryError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def deliver_message(model_label: str, pk: Any, external_id: str) -> dict[str, Any]:
    """Deliver one message; transient exceptions retry the idempotent owner."""

    return run_message_delivery(model_label, pk, external_id)
