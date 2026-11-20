"""Celery wrappers over the web forms' own verbs."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from django.utils import timezone
from rebac import system_context

from angee.base.locks import LockKey, task_lock
from angee.webforms.constants import DELIVER_RESPONSE_TASK, PURGE_RESPONSES_TASK, REDELIVER_RESPONSES_TASK
from angee.webforms.models import DeliveryFailed


@shared_task(name=DELIVER_RESPONSE_TASK)
def deliver_response(pk: int) -> bool:
    """Hand one kept response to its form's target; return whether it is delivered.

    A failure is recorded on the response and tried again by
    :func:`redeliver_responses`, so this task never retries by itself and never
    raises a failure that may quote an answer.
    """

    responses = apps.get_model("webforms", "Response")
    with system_context(reason="webforms.tasks.deliver_response"):
        row = responses._base_manager.filter(pk=pk).first()
    if row is None:
        return False
    try:
        row.deliver()
    except DeliveryFailed:
        return False
    return True


@shared_task(name=REDELIVER_RESPONSES_TASK)
def redeliver_responses(timestamp: int | None = None) -> int:
    """Queue again the undelivered responses whose next try has come (``ResponseManager.redeliver``).

    The advisory task lock keeps a slow run from stacking onto the next tick.
    """

    del timestamp
    with task_lock(LockKey("webforms", ("redeliver_responses",))) as acquired:
        if not acquired:
            return 0
        return int(apps.get_model("webforms", "Response").objects.redeliver(now=timezone.now()))


@shared_task(name=PURGE_RESPONSES_TASK)
def purge_expired_responses(timestamp: int | None = None) -> int:
    """Remove the responses past their form's retention (``ResponseManager.purge_expired``)."""

    del timestamp
    with task_lock(LockKey("webforms", ("purge_expired_responses",))) as acquired:
        if not acquired:
            return 0
        return int(apps.get_model("webforms", "Response").objects.purge_expired(now=timezone.now()))
