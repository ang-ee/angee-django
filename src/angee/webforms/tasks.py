"""Celery wrappers over the web forms' own verbs."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from django.utils import timezone
from rebac import system_context

from angee.webforms.constants import DELIVER_RESPONSE_TASK, PURGE_RESPONSES_TASK


@shared_task(
    name=DELIVER_RESPONSE_TASK, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 5},
)
def deliver_response(pk: int) -> bool:
    """Hand one kept response to its form's target; a failure is recorded on it and tried again."""

    responses = apps.get_model("webforms", "Response")
    with system_context(reason="webforms.tasks.deliver_response"):
        row = responses._base_manager.filter(pk=pk).first()
    if row is None:
        return False
    row.deliver()
    return True


@shared_task(name=PURGE_RESPONSES_TASK)
def purge_expired_responses(timestamp: int | None = None) -> int:
    """Remove the delivered responses past their form's retention."""

    del timestamp
    return int(apps.get_model("webforms", "Response").objects.purge_expired(now=timezone.now()))
