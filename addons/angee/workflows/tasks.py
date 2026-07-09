"""Celery task wrappers for the workflow engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from celery import shared_task
from celery.exceptions import Retry
from django.apps import apps
from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.tasks.locks import record_lock_key, task_lock
from angee.workflows import engine, triggers
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import StepRetryPolicy, TransientStepError, retry_policy_from_config


@shared_task(
    bind=True,
    name="workflows.advance",
    autoretry_for=(Exception,),
    retry_backoff=15,
    retry_kwargs={"max_retries": 5},
)
def advance_workflow_run(self: Any, run_id: int) -> None:
    """Run one short orchestration pass for a workflow run."""

    del self
    with task_lock(record_lock_key("workflows.WorkflowRun", run_id, "advance")) as acquired:
        if not acquired:
            return
        engine.advance(run_id)


@shared_task(bind=True, name="workflows.execute")
def execute_workflow_step(self: Any, step_run_id: int) -> None:
    """Execute one claimed step-run outside the advance lock."""

    with task_lock(record_lock_key("workflows.StepRun", step_run_id, "execute")) as acquired:
        if not acquired:
            return
        try:
            engine.execute(step_run_id)
        except TransientStepError as error:
            _retry_or_journal_exhausted(self, step_run_id, error)


@shared_task(
    bind=True,
    name="workflows.decision_escalate",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def escalate_workflow_decision(self: Any, decision_id: int, attempt: int) -> None:
    """Resolve a decision escalation timer if it still matches the attempt."""

    del self
    with task_lock(record_lock_key("workflows.Decision", decision_id, "escalate")) as acquired:
        if not acquired:
            return
        engine.escalate_decision(decision_id, attempt)


@shared_task(
    bind=True,
    name="workflows.decision_expire",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def expire_workflow_decision(self: Any, decision_id: int, attempt: int) -> None:
    """Resolve a decision expiry timer if it still matches the attempt."""

    del self
    with task_lock(record_lock_key("workflows.Decision", decision_id, "expire")) as acquired:
        if not acquired:
            return
        engine.expire_decision(decision_id, attempt)


@shared_task(
    bind=True,
    name="workflows.sweep",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def sweep_workflow_runs(self: Any, timestamp: int | None = None) -> None:
    """Advance workflow runs whose durable wake time is due."""

    del self, timestamp
    engine.sweep()


@shared_task(
    bind=True,
    name="workflows.reap",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def reap_workflow_step_runs(self: Any, timestamp: int | None = None) -> None:
    """Fail started step-runs whose heartbeat has expired."""

    del self, timestamp
    engine.reap()


@shared_task(
    bind=True,
    name="workflows.schedule_triggers",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def run_workflow_schedule_triggers(self: Any, timestamp: int | None = None) -> None:
    """Start schedule triggers due at the injected periodic timestamp."""

    del self
    triggers.run_due_schedule_triggers(now=_periodic_timestamp(timestamp))


def _retry_or_journal_exhausted(task: Any, step_run_id: int, error: TransientStepError) -> None:
    """Retry a transient step failure or mark the step failed when exhausted."""

    step_run = _step_run_for_id(step_run_id)
    policy = _retry_policy_for_step_run(step_run)
    retries = int(getattr(task.request, "retries", 0))
    if retries + 1 < policy.max_attempts:
        try:
            raise task.retry(exc=error, countdown=_retry_countdown(policy, retries + 1))
        except Retry:
            raise
    _journal_retry_exhausted(step_run, exception=error)
    raise error


def _step_run_for_id(step_run_id: int) -> Any | None:
    """Return the StepRun addressed by one task payload."""

    step_run_model = apps.get_model("workflows", "StepRun")
    with system_context(reason="workflows.retry_policy"):
        return step_run_model.objects.select_related("step").filter(pk=step_run_id).first()


def _retry_policy_for_step_run(step_run: Any | None) -> StepRetryPolicy:
    """Return the static retry policy declared by ``step_run``."""

    if step_run is None or step_run.step_id is None:
        return StepRetryPolicy()
    return retry_policy_from_config(step_run.step.config)


def _retry_countdown(policy: StepRetryPolicy, retry_number: int) -> int:
    """Return the Celery retry countdown for one retry number."""

    if policy.wait:
        return policy.wait
    if policy.linear_wait:
        return policy.linear_wait * retry_number
    if policy.exponential_wait:
        return policy.exponential_wait * (2 ** max(retry_number - 1, 0))
    return 0


def _journal_retry_exhausted(step_run: Any | None, *, exception: BaseException) -> None:
    """Mark a started StepRun failed when no transient retry remains."""

    if step_run is None:
        return
    step_run_model = apps.get_model("workflows", "StepRun")
    run_id: int | None = None
    with system_context(reason="workflows.retry_exhausted"), transaction.atomic():
        locked = step_run_model.objects.lock_if_supported().select_related("run").filter(pk=step_run.pk).first()
        if locked is None or locked.status != StepRunStatus.STARTED:
            return
        locked.mark_failed(error=str(exception), stacktrace="")
        run_id = locked.run_id
    if run_id is not None:
        engine.enqueue_advance(run_id)


def _periodic_timestamp(value: int | None) -> datetime:
    """Return an aware datetime for a periodic Unix timestamp."""

    if value is None:
        return timezone.now()
    return datetime.fromtimestamp(value, tz=timezone.get_current_timezone())
