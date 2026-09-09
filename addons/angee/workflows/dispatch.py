"""Durable workflow-delivery publication for the retained runtime."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from django.apps import apps
from django.db import connections
from django.utils import timezone

from angee.jobs.enqueue import enqueue_task


class WorkflowDispatchKind(StrEnum):
    """Closed workflow delivery kinds owned by the durable dispatcher."""

    ADVANCE = "advance"
    EXECUTE = "execute"
    DECISION_EXPIRE = "decision_expire"
    DECISION_ESCALATE = "decision_escalate"


class DispatchConsumption(StrEnum):
    """Outcome of guarded admission by a domain transition owner."""

    CONSUMED = "consumed"
    DUPLICATE = "duplicate"
    EARLY = "early"
    FENCED = "fenced"


class DispatchPreflightDisposition(StrEnum):
    """Admission result established before a domain owner mutates state."""

    READY = "ready"
    DUPLICATE = "duplicate"
    EARLY = "early"
    FENCED = "fenced"


@dataclass(frozen=True, slots=True)
class WorkflowDispatchEnvelope:
    """Identifier-only transport envelope for one durable delivery intent."""

    dispatch_id: int
    kind: WorkflowDispatchKind
    target_id: int
    generation: int | None
    lease_token: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class DispatchPreflight:
    """Locked dispatch snapshot and its pre-mutation admission result."""

    envelope: WorkflowDispatchEnvelope
    disposition: DispatchPreflightDisposition


DispatchSender = Callable[[WorkflowDispatchEnvelope], None]


def enqueue_dispatch_publisher() -> None:
    """Request one immediate publication pass; periodic recovery remains authoritative."""

    try:
        enqueue_task("workflows.publish_dispatches", kwargs={})
    except Exception:  # noqa: BLE001 - the durable intent remains for periodic recovery.
        return


def publish_due(
    sender: DispatchSender,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, int]:
    """Publish a bounded due batch outside database locks and record telemetry.

    The retained dispatch remains pending after broker acceptance; only its
    exact domain transition owner consumes it after locked admission.
    """

    if limit <= 0:
        raise ValueError("Workflow dispatch publication limit must be positive.")
    timestamp = now or timezone.now()
    dispatch_model = apps.get_model("workflows", "WorkflowDispatch")
    if connections[dispatch_model.objects.db].in_atomic_block:
        raise RuntimeError("Workflow dispatch publication cannot run inside a database transaction.")
    envelopes = dispatch_model.objects.due_envelopes(now=timestamp, limit=limit)
    sent = 0
    failed = 0
    for envelope in envelopes:
        try:
            sender(envelope)
        except Exception:  # noqa: BLE001 - transport failure is bounded telemetry.
            failed += 1
            dispatch_model.objects.record_publication(
                envelope.dispatch_id,
                attempted_at=timestamp,
                error="Transport send failed.",
            )
        else:
            sent += 1
            dispatch_model.objects.record_publication(
                envelope.dispatch_id,
                attempted_at=timestamp,
                error="",
            )
    return {"selected": len(envelopes), "sent": sent, "failed": failed}
