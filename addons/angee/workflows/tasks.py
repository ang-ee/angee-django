"""Celery task wrappers for the workflow engine."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from celery import shared_task
from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.jobs.enqueue import enqueue_task
from angee.workflows import dispatch as workflow_dispatch
from angee.workflows import engine, triggers
from angee.workflows.dispatch import WorkflowDispatchEnvelope, WorkflowDispatchKind


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


@shared_task(bind=True, name="workflows.dispatch")
def consume_workflow_dispatch(
    self: Any,
    dispatch_id: int,
    kind: str,
    target_id: int,
    generation: int | None = None,
    lease_token: str | None = None,
    using: str | None = None,
) -> None:
    """Consume one identifier-only durable workflow envelope."""

    del self
    parsed = WorkflowDispatchKind(kind)
    parsed_lease = uuid.UUID(lease_token) if lease_token is not None else None
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    alias = get_write_alias(dispatch_model, using=using)
    with system_context(reason="workflows.dispatch.envelope"):
        durable = dispatch_model.objects.db_manager(alias).select_related("step_attempt").get(pk=dispatch_id).envelope
    supplied = WorkflowDispatchEnvelope(dispatch_id, parsed, target_id, generation, parsed_lease)
    if supplied != durable:
        raise ValidationError({"dispatch": "Transport envelope does not match its durable intent."})
    if parsed == WorkflowDispatchKind.ADVANCE:
        engine.advance_dispatch(dispatch_id, expected_run_id=target_id, using=alias)
    elif parsed == WorkflowDispatchKind.EXECUTE:
        engine.execute_dispatch(dispatch_id, target_id, cast(uuid.UUID, parsed_lease), using=alias)
    elif parsed == WorkflowDispatchKind.DECISION_ESCALATE:
        engine.escalate_decision_dispatch(
            dispatch_id, expected_decision_id=target_id, expected_generation=generation, using=alias
        )
    elif parsed == WorkflowDispatchKind.DECISION_EXPIRE:
        engine.expire_decision_dispatch(
            dispatch_id, expected_decision_id=target_id, expected_generation=generation, using=alias
        )
    elif parsed == WorkflowDispatchKind.ARTIFACT_DELIVERY:
        engine.deliver_artifact_dispatch(dispatch_id, using=alias)
    elif parsed == WorkflowDispatchKind.CHILD_CANCEL:
        engine.cancel_child_dispatch(dispatch_id, expected_child_id=target_id, using=alias)
    elif parsed == WorkflowDispatchKind.RUN_CANCEL:
        engine.cancel_run_dispatch(dispatch_id, expected_run_id=target_id, using=alias)


@shared_task(bind=True, name="workflows.publish_dispatches")
def publish_workflow_dispatches(self: Any, timestamp: int | None = None, using: str | None = None) -> None:
    """Publish one bounded batch of due durable workflow intents."""

    del self
    alias = get_write_alias(apps.get_model("workflows", "WorkflowDispatch"), using=using)
    workflow_dispatch.publish_due(
        lambda envelope: _send_dispatch(envelope, using=alias),
        now=_periodic_timestamp(timestamp), limit=100, using=alias,
    )


def _send_dispatch(envelope: WorkflowDispatchEnvelope, *, using: str) -> None:
    enqueue_task(
        "workflows.dispatch",
        kwargs={
            "dispatch_id": envelope.dispatch_id,
            "using": using,
            "kind": envelope.kind.value,
            "target_id": envelope.target_id,
            "generation": envelope.generation,
            "lease_token": str(envelope.lease_token) if envelope.lease_token else None,
        },
        eta=None,
    )


def _periodic_timestamp(value: int | None) -> datetime:
    """Return an aware datetime for a periodic Unix timestamp."""

    if value is None:
        return timezone.now()
    return datetime.fromtimestamp(value, tz=timezone.get_current_timezone())
