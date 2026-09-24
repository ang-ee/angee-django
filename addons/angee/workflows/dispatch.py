"""Durable workflow-delivery publication for the retained runtime."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import partial, reduce
from operator import or_
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connections, models
from django.utils import timezone
from django.utils.module_loading import import_string

from angee.base.db import get_write_alias, related_on
from angee.jobs.enqueue import enqueue_task
from angee.workflows.states import Verdict


class WorkflowDispatchKind(models.TextChoices, StrEnum):
    """Closed workflow delivery kinds owned by the durable dispatcher."""

    ADVANCE = "advance"
    EXECUTE = "execute"
    DECISION_EXPIRE = "decision_expire"
    DECISION_ESCALATE = "decision_escalate"
    ARTIFACT_DELIVERY = "artifact_delivery"
    CHILD_CANCEL = "child_cancel"
    RUN_CANCEL = "run_cancel"
    RUN_SETTLE = "run_settle"

    @property
    def spec(self) -> DispatchKindSpec:
        """Return this kind's complete delivery declaration."""

        return DISPATCH_KINDS[self]


@dataclass(frozen=True, slots=True)
class DispatchLock:
    """A dispatch-relative relation to lock, in declared ancestry order."""

    path: str
    execution_ancestry: bool = False


@dataclass(slots=True)
class DispatchTarget:
    """Locked domain target and the result of one admitted delivery.

    Execution admits under the locks, then invokes the implementation after the
    delivery transaction exits. All other handlers finish in that transaction.
    """

    row: Any
    dispatch: Any
    result: dict[str, int]
    after_unlock: Callable[[], dict[str, int]] | None = None


@dataclass(frozen=True, slots=True)
class DispatchKindSpec:
    """One source for target shape, locking, uniqueness and domain dispatch."""

    target_field: str
    lock_plan: tuple[DispatchLock, ...]
    uniqueness: tuple[str, ...]
    handler_path: str
    result_fields: tuple[str, ...]
    required_fields: tuple[str, ...]
    lease_field: str | None = None
    handler_kwargs: tuple[tuple[str, Any], ...] = ()
    error_handler: str | None = None

    @property
    def handler(self) -> Callable[..., bool]:
        # Dispatch declarations load during Django's model phase; the engine's
        # composed-model operations become importable only when delivery starts.
        return partial(import_string(self.handler_path), **dict(self.handler_kwargs))

    def envelope(self, dispatch: Any) -> WorkflowDispatchEnvelope:
        target_id = getattr(dispatch, self.target_field)
        if target_id is None:
            raise ValueError("Workflow dispatch target does not match its kind.")
        lease_token = None
        if self.lease_field is not None:
            lease_token = getattr(dispatch, "_dispatch_lease_token", models.DEFERRED)
            if lease_token is models.DEFERRED:
                attempt = related_on(dispatch, self.lease_field, using=dispatch._state.db)
                if attempt is None:
                    raise ValueError("Execution dispatch requires its retained attempt.")
                lease_token = attempt.lease_token
        return WorkflowDispatchEnvelope(dispatch.pk, dispatch.kind, target_id, dispatch.generation, lease_token)


_RUN_LOCKS = (DispatchLock("run", execution_ancestry=True),)
_ATTEMPT_LOCKS = (
    DispatchLock("step_attempt__step_run__run", execution_ancestry=True),
    DispatchLock("step_attempt__step_run"),
    DispatchLock("step_attempt"),
)
_DECISION_LOCKS = (
    DispatchLock("decision__step_run__run", execution_ancestry=True),
    DispatchLock("decision__step_run"),
    DispatchLock("decision__suspension_attempt"),
    DispatchLock("decision"),
)
DISPATCH_KINDS = {
    WorkflowDispatchKind.ADVANCE: DispatchKindSpec(
        "run_id",
        _RUN_LOCKS,
        (),
        "angee.workflows.engine.advance_locked",
        ("claimed",),
        ("run",),
        error_handler="record_advance_error",
    ),
    WorkflowDispatchKind.EXECUTE: DispatchKindSpec(
        "step_attempt_id",
        _ATTEMPT_LOCKS,
        ("step_attempt",),
        "angee.workflows.engine.admit_execution",
        ("executed",),
        ("step_attempt",),
        lease_field="step_attempt",
    ),
    WorkflowDispatchKind.DECISION_EXPIRE: DispatchKindSpec(
        "decision_id",
        _DECISION_LOCKS,
        ("kind", "decision", "generation"),
        "angee.workflows.engine.resolve_decision_timer",
        ("resolved",),
        ("decision", "generation"),
        handler_kwargs=(("verdict", Verdict.EXPIRED),),
    ),
    WorkflowDispatchKind.DECISION_ESCALATE: DispatchKindSpec(
        "decision_id",
        _DECISION_LOCKS,
        ("kind", "decision", "generation"),
        "angee.workflows.engine.resolve_decision_timer",
        ("resolved",),
        ("decision", "generation"),
        handler_kwargs=(("verdict", Verdict.ESCALATED),),
    ),
    WorkflowDispatchKind.ARTIFACT_DELIVERY: DispatchKindSpec(
        "pk",
        (),
        (),
        "angee.workflows.engine.deliver_artifact_locked",
        ("runs", "woken"),
        ("artifact_content_type", "artifact_object_id"),
    ),
    WorkflowDispatchKind.CHILD_CANCEL: DispatchKindSpec(
        "run_id",
        (*_RUN_LOCKS, DispatchLock("run__parent_step_run")),
        ("kind", "run"),
        "angee.workflows.engine.cancel_child_locked",
        ("canceled",),
        ("run",),
    ),
    WorkflowDispatchKind.RUN_CANCEL: DispatchKindSpec(
        "run_id",
        _RUN_LOCKS,
        ("kind", "run"),
        "angee.workflows.engine.cancel_run_locked",
        ("canceled",),
        ("run",),
    ),
    WorkflowDispatchKind.RUN_SETTLE: DispatchKindSpec(
        "run_id",
        _RUN_LOCKS,
        ("kind", "run"),
        "angee.workflows.engine.settle_subject",
        ("settled",),
        ("run",),
    ),
}


def dispatch_constraints() -> tuple[models.BaseConstraint, ...]:
    """Derive database envelope and unique-intent rules from every kind."""

    if set(DISPATCH_KINDS) != set(WorkflowDispatchKind):
        raise ImproperlyConfigured("Every workflow dispatch kind requires exactly one specification.")
    fields = set().union(*(spec.required_fields for spec in DISPATCH_KINDS.values()))
    shapes = [
        models.Q(kind=kind, **{f"{name}__isnull": name not in spec.required_fields for name in sorted(fields)})
        for kind, spec in DISPATCH_KINDS.items()
    ]
    return (
        models.CheckConstraint(condition=reduce(or_, shapes), name="chk_wfd_target_shape"),
        *(
            models.UniqueConstraint(fields=spec.uniqueness, condition=models.Q(kind=kind), name=f"uniq_wfd_{kind}")
            for kind, spec in DISPATCH_KINDS.items()
            if spec.uniqueness
        ),
    )


@dataclass(frozen=True, slots=True)
class WorkflowDispatchEnvelope:
    """Identifier-only transport envelope for one durable delivery intent."""

    dispatch_id: int
    kind: WorkflowDispatchKind
    target_id: int
    generation: int | None
    lease_token: uuid.UUID | None


DispatchSender = Callable[[WorkflowDispatchEnvelope], None]


def enqueue_dispatch_publisher(*, using: str | None = None) -> None:
    """Request one immediate publication pass; periodic recovery remains authoritative."""

    try:
        enqueue_task("workflows.publish_dispatches", kwargs={"using": using})
    except Exception:  # noqa: BLE001 - the durable intent remains for periodic recovery.
        return


def publish_due(
    sender: DispatchSender,
    *,
    now: datetime | None = None,
    limit: int = 100,
    using: str | None = None,
) -> dict[str, int]:
    """Publish a bounded due batch outside database locks and record telemetry.

    The retained dispatch remains pending after broker acceptance; the dispatch
    manager consumes it together with its admitted domain transition.
    """

    if limit <= 0:
        raise ValueError("Workflow dispatch publication limit must be positive.")
    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    alias = get_write_alias(dispatch_model, using=using)
    manager = dispatch_model.objects.db_manager(alias)
    if connections[alias].in_atomic_block:
        raise RuntimeError("Workflow dispatch publication cannot run inside a database transaction.")
    envelopes = manager.due_envelopes(now=timestamp, limit=limit)
    sent = 0
    failed = 0
    for envelope in envelopes:
        try:
            sender(envelope)
        except Exception:  # noqa: BLE001 - transport failure is bounded telemetry.
            failed += 1
            manager.record_publication(
                envelope.dispatch_id,
                attempted_at=timestamp,
                error="Transport send failed.",
            )
        else:
            sent += 1
            manager.record_publication(
                envelope.dispatch_id,
                attempted_at=timestamp,
                error="",
            )
    return {"selected": len(envelopes), "sent": sent, "failed": failed}
