"""Identifier-only task entrypoints for workflow manager verbs."""

from celery import shared_task
from django.apps import apps

from angee.jobs.locks import LockKey, task_lock


@shared_task(name="workflows.execute")
def execute(step_run_id: int) -> bool:
    """Execute one ready step through its declared transaction mode."""
    return apps.get_model("workflows", "StepRun").objects.execute(step_run_id)


@shared_task(name="workflows.tick")
def tick() -> dict[str, int]:
    """Recover due rows on the shared worker, using the database clock."""
    with task_lock(LockKey("workflows", ("tick",))) as acquired:
        return apps.get_model("workflows", "StepRun").objects.tick() if acquired else {}
