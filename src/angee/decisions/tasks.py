"""Celery entrypoints for decisions' scheduled reminders."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from rebac import system_context

from angee.jobs.scheduler import periodic_now


@shared_task(name="decisions.remind_due")
def remind_due(timestamp: int | None = None) -> int:
    """Remind every open question whose reminder instant has arrived, once each."""

    decision_model = apps.get_model("decisions", "Decision")
    with system_context(reason="decisions.tasks.remind_due"):
        return decision_model.objects.remind_due(now=periodic_now(timestamp))
