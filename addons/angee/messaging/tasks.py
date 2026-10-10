"""Celery task wrappers for outbound messaging delivery."""

from __future__ import annotations

from typing import Any

from celery import shared_task
from django.apps import apps

from angee.messaging.constants import DELIVER_MESSAGE_TASK, RELEASE_MESSAGES_TASK
from angee.messaging.delivery import run_message_delivery


@shared_task(name=DELIVER_MESSAGE_TASK)
def deliver_message(model_label: str, pk: Any, external_id: str) -> dict[str, Any]:
    """Deliver one message; the manager persists retries for the sweep."""

    return run_message_delivery(model_label, pk, external_id)


@shared_task(name=RELEASE_MESSAGES_TASK)
def release_held_messages() -> int:
    """Dispatch due held messages and recover expired broker submissions."""

    return apps.get_model("messaging", "Message").objects.release_held_messages()
