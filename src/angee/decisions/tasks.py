"""Celery entrypoints for decisions' scheduled reminders."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps

from angee.jobs.scheduler import periodic_now


@shared_task(name="decisions.remind_due")
def remind_due(timestamp: int | None = None) -> int:
    """Remind the open questions whose reminder instant has arrived, once each.

    The manager elevates only its own selection and stamps, so reminder receivers run
    without this task holding a system context.
    """

    decision_model = apps.get_model("decisions", "Decision")
    return decision_model.objects.remind_due(now=periodic_now(timestamp))
