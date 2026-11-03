"""Identifier-only task entrypoints for workflow owners."""

from celery import shared_task
from django.apps import apps
from django.db import router
from rebac import SubjectRef, database_context

from angee.base.locks import LockKey, task_lock
from angee.workflows.runner import runner


@shared_task(name="workflows.execute")
def execute(step_run_id: int, *, using: str | None = None) -> bool:
    """Execute one ready step through its declared transaction mode."""
    return runner.execute(step_run_id, using=using)


@shared_task(name="workflows.wake_run")
def wake_run(run_id: int, *, using: str | None = None) -> int:
    """Wake target waiters after terminal settlement releases the child's lock."""
    return runner.wake_runs(run_id, using=using)


@shared_task(name="workflows.wake_decisions")
def wake_decisions(decision_id: int, *, using: str | None = None) -> int:
    """Wake one answered decision's waiters after its verdict commits."""
    return runner.wake_decisions(decision_id, using=using)


@shared_task(name="workflows.cancel")
def cancel(run_id: int, actor: str, *, using: str | None = None) -> None:
    """Cancel outside the requesting transaction, retaining its permission scope."""
    model = apps.get_model("workflows", "WorkflowRun")
    using = router.db_for_write(model) if using is None else using
    with database_context(using):
        run = model.system_queryset().using(using).filter(pk=run_id).first()
        if run is not None:
            model.objects.db_manager(using).cancel(run, actor=SubjectRef.parse(actor), timeout=None)


@shared_task(name="workflows.wake_records")
def wake_records(content_type_id: int, object_id: int) -> int:
    """Wake changed-record waiters after the source transaction releases its locks."""
    return runner.wake_records(
        content_type_id=content_type_id,
        object_id=object_id,
    )


@shared_task(name="workflows.tick")
def tick() -> dict[str, int]:
    """Recover due rows on the shared worker, using the database clock."""
    with task_lock(LockKey("workflows", ("tick",))) as acquired:
        return runner.tick() if acquired else {}
