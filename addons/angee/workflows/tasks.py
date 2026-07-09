"""Procrastinate task wrappers for the workflow engine."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from procrastinate import RetryStrategy
from procrastinate import jobs as procrastinate_jobs
from procrastinate.contrib.django import app
from rebac import system_context

from angee.workflows import engine, triggers
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import StepRetryPolicy, TransientStepError, retry_policy_from_config


class StepRunRetryStrategy(RetryStrategy):
    """Procrastinate retry strategy driven by a step row's static config."""

    def __init__(self) -> None:
        """Retry only transient step failures."""

        super().__init__(retry_exceptions=(TransientStepError,))

    def get_retry_decision(self, *, exception: BaseException, job: procrastinate_jobs.Job) -> Any:
        """Return the retry decision for ``job``'s StepRun."""

        if not isinstance(exception, TransientStepError):
            return None
        step_run = _step_run_for_job(job)
        policy = _retry_policy_for_step_run(step_run)
        decision = RetryStrategy(
            max_attempts=policy.max_attempts,
            wait=policy.wait,
            linear_wait=policy.linear_wait,
            exponential_wait=policy.exponential_wait,
            retry_exceptions=(TransientStepError,),
        ).get_retry_decision(exception=exception, job=job)
        if decision is None:
            _journal_retry_exhausted(step_run, exception=exception)
        return decision


@app.task(name="workflows.advance", retry=RetryStrategy(max_attempts=5, exponential_wait=15))
def advance_workflow_run(run_id: int) -> None:
    """Run one short orchestration pass for a workflow run."""

    engine.advance(run_id)


@app.task(name="workflows.execute", retry=StepRunRetryStrategy())
def execute_workflow_step(step_run_id: int) -> None:
    """Execute one claimed step-run outside the advance lock."""

    engine.execute(step_run_id)


@app.task(name="workflows.decision_escalate", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def escalate_workflow_decision(decision_id: int, attempt: int) -> None:
    """Resolve a decision escalation timer if it still matches the attempt."""

    engine.escalate_decision(decision_id, attempt)


@app.task(name="workflows.decision_expire", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def expire_workflow_decision(decision_id: int, attempt: int) -> None:
    """Resolve a decision expiry timer if it still matches the attempt."""

    engine.expire_decision(decision_id, attempt)


@app.periodic(cron="* * * * *", periodic_id="workflows.sweep")
@app.task(name="workflows.sweep", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def sweep_workflow_runs(timestamp: int) -> None:
    """Advance workflow runs whose durable wake time is due."""

    del timestamp
    engine.sweep()


@app.periodic(cron="* * * * *", periodic_id="workflows.reap")
@app.task(name="workflows.reap", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def reap_workflow_step_runs(timestamp: int) -> None:
    """Fail started step-runs whose heartbeat has expired."""

    del timestamp
    engine.reap()


@app.periodic(cron="* * * * *", periodic_id="workflows.schedule_triggers")
@app.task(name="workflows.schedule_triggers", retry=RetryStrategy(max_attempts=3, exponential_wait=30))
def run_workflow_schedule_triggers(timestamp: int) -> None:
    """Start schedule triggers due at the injected periodic timestamp."""

    triggers.run_due_schedule_triggers(now=_periodic_timestamp(timestamp))


def _step_run_for_job(job: procrastinate_jobs.Job) -> Any | None:
    """Return the StepRun addressed by a Procrastinate job."""

    raw_step_run_id = job.task_kwargs.get("step_run_id")
    if not isinstance(raw_step_run_id, int):
        return None

    step_run_model = apps.get_model("workflows", "StepRun")
    with system_context(reason="workflows.retry_policy"):
        return step_run_model.objects.select_related("step").filter(pk=raw_step_run_id).first()


def _retry_policy_for_step_run(step_run: Any | None) -> StepRetryPolicy:
    """Return the static retry policy declared by ``step_run``."""

    if step_run is None or step_run.step_id is None:
        return StepRetryPolicy()
    return retry_policy_from_config(step_run.step.config)


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


def _periodic_timestamp(value: int) -> datetime:
    """Return an aware datetime for Procrastinate's periodic Unix timestamp."""

    return datetime.fromtimestamp(value, tz=timezone.get_current_timezone())
