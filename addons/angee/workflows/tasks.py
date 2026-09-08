"""Celery task wrappers for the workflow engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from celery import shared_task
from celery.exceptions import Retry
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from rebac import system_context

from angee.jobs.enqueue import enqueue_task
from angee.jobs.locks import record_lock_key, task_lock
from angee.workflows import dispatch as workflow_dispatch
from angee.workflows import engine, triggers
from angee.workflows.dispatch import WorkflowDispatchEnvelope, WorkflowDispatchKind
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
    """Translate a legacy wake into a durable ADVANCE pulse."""

    del self
    run_model = apps.get_model("workflows", "WorkflowRun")
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    with system_context(reason="workflows.legacy_advance"), transaction.atomic():
        run = run_model.objects.filter(pk=run_id).first()
        if run is None:
            return
        dispatch_model.objects.schedule_advance(run, available_at=timezone.now())
    workflow_dispatch.publish_due(_send_dispatch, now=timezone.now(), limit=100)


@shared_task(bind=True, name="workflows.execute")
def execute_workflow_step(self: Any, step_run_id: int) -> None:
    """Execute one claimed step-run outside the advance lock."""

    with task_lock(record_lock_key("workflows.StepRun", step_run_id, "execute")) as acquired:
        if not acquired:
            return
        try:
            step_run_model = apps.get_model("workflows", "StepRun")
            with system_context(reason="workflows.legacy_execute"):
                retained = step_run_model.objects.filter(pk=step_run_id).filter(
                    models.Q(effect_key__isnull=False)
                    | models.Q(current_attempt__isnull=False)
                    | models.Q(attempts__isnull=False)
                ).exists()
            if retained:
                return
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
    name="workflows.decisions",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_kwargs={"max_retries": 3},
)
def sweep_workflow_decisions(self: Any, timestamp: int | None = None) -> None:
    """Resolve workflow decisions whose durable deadlines are due."""

    del self
    engine.sweep_decisions(now=_periodic_timestamp(timestamp))


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
            raise task.retry(exc=error, countdown=policy.delay_for(retries + 1))
        except Retry:
            raise
    _journal_retry_exhausted(step_run, exception=error)
    raise error


@shared_task(bind=True, name="workflows.dispatch")
def consume_workflow_dispatch(
    self: Any,
    dispatch_id: int,
    kind: str,
    target_id: int,
    generation: int | None = None,
    lease_token: str | None = None,
) -> None:
    """Consume one identifier-only durable workflow envelope."""

    del self
    parsed = WorkflowDispatchKind(kind)
    parsed_lease = uuid.UUID(lease_token) if lease_token is not None else None
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    with system_context(reason="workflows.dispatch.envelope"):
        durable = dispatch_model.objects.select_related("step_attempt").get(pk=dispatch_id).envelope
    supplied = WorkflowDispatchEnvelope(dispatch_id, parsed, target_id, generation, parsed_lease)
    if supplied != durable:
        raise ValidationError({"dispatch": "Transport envelope does not match its durable intent."})
    if parsed == WorkflowDispatchKind.ADVANCE:
        engine.advance_dispatch(dispatch_id, expected_run_id=target_id)
    elif parsed == WorkflowDispatchKind.EXECUTE:
        engine.execute_dispatch(dispatch_id, target_id, cast(uuid.UUID, parsed_lease))
    elif parsed == WorkflowDispatchKind.DECISION_ESCALATE:
        engine.escalate_decision_dispatch(
            dispatch_id, expected_decision_id=target_id, expected_generation=generation
        )
    else:
        engine.expire_decision_dispatch(
            dispatch_id, expected_decision_id=target_id, expected_generation=generation
        )


@shared_task(bind=True, name="workflows.publish_dispatches")
def publish_workflow_dispatches(self: Any, timestamp: int | None = None) -> None:
    """Publish one bounded batch of due durable workflow intents."""

    del self
    workflow_dispatch.publish_due(_send_dispatch, now=_periodic_timestamp(timestamp), limit=100)


def _send_dispatch(envelope: WorkflowDispatchEnvelope) -> None:
    enqueue_task(
        "workflows.dispatch",
        kwargs={
            "dispatch_id": envelope.dispatch_id,
            "kind": envelope.kind.value,
            "target_id": envelope.target_id,
            "generation": envelope.generation,
            "lease_token": str(envelope.lease_token) if envelope.lease_token else None,
        },
        eta=None,
    )


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
