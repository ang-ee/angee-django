"""Identifier-only task entrypoints for workflow manager verbs."""

from celery import shared_task
from django.apps import apps
from rebac import SubjectRef

from angee.base.scoping import system_queryset
from angee.jobs.locks import LockKey, task_lock


@shared_task(name="workflows.execute")
def execute(step_run_id: int) -> bool:
    """Execute one ready step through its declared transaction mode."""
    return apps.get_model("workflows", "StepRun").objects.execute(step_run_id)


@shared_task(name="workflows.wake_run")
def wake_run(run_id: int) -> int:
    """Wake target waiters after terminal settlement releases the child's lock."""
    return apps.get_model("workflows", "StepRun").objects.wake_runs(run_id)


@shared_task(name="workflows.cancel")
def cancel(run_id: int, actor: str) -> None:
    """Cancel outside the requesting transaction, retaining its permission scope."""
    model = apps.get_model("workflows", "WorkflowRun")
    run = system_queryset(model).filter(pk=run_id).first()
    if run is not None:
        model.objects.cancel(run, actor=SubjectRef.parse(actor), timeout=None)


@shared_task(name="workflows.tick")
def tick() -> dict[str, int]:
    """Recover due rows on the shared worker, using the database clock."""
    with task_lock(LockKey("workflows", ("tick",))) as acquired:
        return apps.get_model("workflows", "StepRun").objects.tick() if acquired else {}
