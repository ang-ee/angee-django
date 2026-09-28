"""Periodic expiry through the shared jobs seam."""

from celery import shared_task
from django.apps import apps

from angee.jobs.locks import LockKey, task_lock


@shared_task(name="decisions.expire")
def expire() -> int:
    """Close due decisions while holding the shared expiry task lock."""
    with task_lock(LockKey("decisions", ("expire",))) as acquired:
        return apps.get_model("decisions", "Decision").objects.expire_due() if acquired else 0
