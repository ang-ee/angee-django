"""Paged synchronization and coverage composed over integrate's durable owners."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import timedelta
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from angee.base.identity import public_id_of
from angee.decisions.contracts import DecisionContext, DecisionProposal, DecisionRecordReference, DecisionRequest
from angee.integrate.impl import AdapterContractError, BridgeImpl
from angee.integrate.models import Bridge
from angee.integrate.states import StreamPhase, SyncFailureKind
from angee.integrate.streams import advance_stream, begin_stream_cycle, open_stream
from angee.workflows.context import StepContext
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.steps import Retryable, RetryPolicy, Settlement, Step, StepMode, Superseded


class StreamReference(BaseModel):
    """A declared partition, independent of its current stream generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(min_length=1)
    partition: str = ""


class StreamStageInput(StreamReference):
    """One mapped partition and its page bound; SyncStream alone owns the cursor."""

    page_bound: int = Field(default=100, ge=1)


class StreamStageOutput(BaseModel):
    """Acknowledged page counts and current stream/failure evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    counts: dict[str, int]
    failure_ids: list[str]
    evidence: list[DecisionRecordReference]

    @classmethod
    def for_streams(cls, streams: list[Any], *, counts: dict[str, int]) -> StreamStageOutput:
        """Project unresolved truth through its manager for the admitted partitions."""
        failures = [row for stream in streams for row in stream.sync_failures(open_only=True)]
        links = {row.link.pk: row.link for row in failures if row.link is not None}

        return cls(
            counts=counts,
            failure_ids=[row.id for row in failures],
            evidence=[_reference(row) for row in [*streams, *links.values()]],
        )


class CoverageInput(BaseModel):
    """The distinct partition set whose coverage the admitted cycle requires."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    streams: list[StreamReference] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_streams(self) -> CoverageInput:
        """Reject repeated partitions rather than rescanning them twice."""
        identities = [(stream.key, stream.partition) for stream in self.streams]
        if len(identities) != len(set(identities)):
            raise ValueError("Coverage streams must be distinct.")
        return self

    def current_streams(self, bridge: Bridge) -> list[Any]:
        """Resolve each declared partition through the durable epoch owner."""
        manager = apps.get_model("integrate", "SyncStream").objects
        return [
            _pull_only(manager.current_for_bridge(bridge, row.key).get(partition=row.partition)) for row in self.streams
        ]


class RescanConfig(BaseModel):
    """Bounded work and the polling interval for externally resolved failures."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    reconcile_seconds: int = Field(default=60, ge=1)
    rescan_bound: int = Field(default=100, ge=1)


class StreamStage(Step[StreamStageInput, StreamStageOutput, None]):
    """Commit one driver page per attempt and resume from the stream's cursor.

    Workflow checkpoints retain counts and continuation digests, never cursors. A worker
    lost after the page commits cannot replay that page; its unacknowledged
    count is absent because stream advancement and workflow settlement commit
    independently. Fan-out belongs to the native map step.
    """

    key = "integrate_stream"
    label = "Advance sync stream"
    mode = StepMode.IO
    effect_idempotent = True
    retry = RetryPolicy(max_attempts=3)

    def run(self, ctx: StepContext) -> Settlement:
        """Apply exactly one bounded page with integrate's page/cursor transaction."""
        bridge = _bridge_subject(ctx)
        with _stream_io(ctx, bridge) as adapter:
            stream = _pull_only(open_stream(bridge, ctx.input.key, ctx.input.partition, adapter))
            page = advance_stream(stream, adapter, page_bound=ctx.input.page_bound)
        progress, resets = page.check_continuation(
            previous=ctx.state.get("progress"),
            resets=ctx.state.get("resets", 0),
        )
        cycle_items = ctx.state.get("cycle_items", 0) + page.count
        if not page.exhausted:
            return ctx.next_page({"cycle_items": cycle_items, "progress": progress, "resets": resets})
        return ctx.done(
            StreamStageOutput.for_streams(
                [page.stream],
                counts={"page_items": page.count, "cycle_items": cycle_items},
            )
        )


class Rescan(Step[CoverageInput, StreamStageOutput, RescanConfig]):
    """Rescan one partition per attempt and wait while coverage remains open.

    The driver owns due-identity selection, conflict exclusion and baseline
    fallback. A baseline's persisted phase survives lost workflow settlement;
    continuation carries the partition index and progress digest, never a cursor.
    """

    key = "integrate_rescan"
    label = "Recheck sync coverage"
    mode = StepMode.IO
    effect_idempotent = True
    retry = RetryPolicy(max_attempts=3)

    def run(self, ctx: StepContext) -> Settlement:
        """Bound recovery work before testing the current failure truth."""
        bridge = _bridge_subject(ctx)
        streams = ctx.input.current_streams(bridge)
        index = ctx.state.get("rescan_index", 0)
        stream = streams[index]
        page = None
        with _stream_io(ctx, bridge) as adapter:
            if stream.phase != StreamPhase.BASELINE:
                stream = begin_stream_cycle(stream, adapter, page_bound=ctx.config.rescan_bound)
            if stream.resync_required or stream.phase == StreamPhase.BASELINE:
                page = advance_stream(stream, adapter, page_bound=ctx.config.rescan_bound)
        if page is not None:
            progress, resets = page.check_continuation(
                previous=ctx.state.get("progress"),
                resets=ctx.state.get("resets", 0),
            )
            if not page.exhausted:
                return ctx.next_page({"rescan_index": index, "progress": progress, "resets": resets})
        if index + 1 < len(streams):
            return ctx.next_page({"rescan_index": index + 1})
        output = StreamStageOutput.for_streams(
            ctx.input.current_streams(bridge),
            counts={"streams": len(streams)},
        )
        if output.failure_ids:
            return ctx.wait(until=ctx.now + timedelta(seconds=ctx.config.reconcile_seconds), state={"rescan_index": 0})
        return ctx.done(output)


class ConflictReview(DecisionStep[CoverageInput, StreamStageOutput, None]):
    """Ask independent questions; direct domain edits resolve the failures."""

    key = "integrate_conflicts"
    label = "Review sync conflicts"

    def ask(self, ctx: StepContext) -> Settlement:
        streams = ctx.input.current_streams(_bridge_subject(ctx))
        conflicts = [
            row
            for stream in streams
            for row in stream.sync_failures(open_only=True)
            if row.kind == SyncFailureKind.CONFLICT
        ]
        # Decisions reference permission-bearing owners, never a JSON state slot.
        records = tuple(
            {
                (owner._meta.label, owner.pk): owner
                for row in conflicts
                for owner in (row.link if row.link is not None else row.stream,)
            }.values()
        )

        if records:
            return ctx.ask(
                DecisionRequest(
                    kind="review-sync-conflict",
                    records=records,
                    assignees=(ctx.actor,),
                    requester=None,
                    proposal=DecisionProposal.model_validate(
                        {
                            "multiple": True,
                            "alternatives": [
                                {"key": public_id_of(row), "label": f"Recheck {row}", "outcome": "done"}
                                for row in records
                            ],
                        }
                    ),
                    context=DecisionContext(references=tuple(_reference(row.stream) for row in conflicts)),
                )
            )
        return ctx.done(StreamStageOutput.for_streams(streams, counts={"streams": len(streams)}))

    def continue_with(self, ctx: StepContext, decision: Any, outcome: str) -> Settlement:
        streams = ctx.input.current_streams(_bridge_subject(ctx))
        if any(
            row.kind == SyncFailureKind.CONFLICT for stream in streams for row in stream.sync_failures(open_only=True)
        ):
            raise ValidationError({"conflicts": "Resolve the sync conflicts before retrying this step."})
        return ctx.done(StreamStageOutput.for_streams(streams, counts={"streams": len(streams)}))


def _pull_only(stream: Any) -> Any:
    """Admit a pull-only partition: no cycle stage pushes, so none could write or retry a refused write."""
    if stream.writes_back:
        raise ValidationError(
            f"Workflow sync cycles pull only; stream {stream.key!r} partition {stream.partition!r} writes back."
        )
    return stream


def _bridge_subject(ctx: StepContext) -> Bridge:
    """Require a bridge after StepContext resolves the actor-readable subject."""
    bridge = ctx.subject
    if not isinstance(bridge, Bridge):
        raise ValidationError("Sync steps require a Bridge workflow subject.")
    return bridge


@contextmanager
def _stream_io(ctx: StepContext, bridge: Bridge) -> Iterator[BridgeImpl]:
    """Fence transport and classify infrastructure failures for native retries."""
    ctx.heartbeat()
    ctx.begin_effect()
    try:
        with closing(bridge.backend) as adapter:
            yield adapter
    except (
        AdapterContractError,
        ImproperlyConfigured,
        PermissionDenied,
        ValidationError,
        Retryable,
        Superseded,
        SoftTimeLimitExceeded,
    ):
        raise
    except Exception as error:  # noqa: BLE001 -- adapters quarantine semantic errors in the page owner.
        raise Retryable("Stream transport failed.") from error


def _reference(record: Any) -> DecisionRecordReference:
    return DecisionRecordReference(model=record._meta.label, id=public_id_of(record))
