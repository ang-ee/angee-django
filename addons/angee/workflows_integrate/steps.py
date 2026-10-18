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
from angee.integrate.net import AddressResolutionError
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

    def current_stream(self, bridge: Bridge) -> Any:
        """Resolve the opened partition's current epoch through its durable owner."""
        manager = apps.get_model("integrate", "SyncStream").objects
        return _pull_only(manager.current_for_bridge(bridge, self.key).get(partition=self.partition))


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
        return [row.current_stream(bridge) for row in self.streams]


class RescanConfig(BaseModel):
    """Bounded work and the interval within which a due retry keeps the rescan waiting."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    reconcile_seconds: int = Field(default=60, ge=1)
    rescan_bound: int = Field(default=100, ge=1)


class StreamStage(Step[StreamStageInput, StreamStageOutput, None]):
    """Commit one driver page per attempt and resume from the stream's cursor.

    Workflow checkpoints retain counts and continuation digests, never cursors. A worker
    lost after the page commits cannot replay that page; its unacknowledged
    count is absent because stream advancement and workflow settlement commit
    independently. Fan-out belongs to the native map step.

    A stage run is one drain of its partition: its first page opens the stream, which
    reconciles it with the adapter's current declaration (``open_stream``), and each
    later page resumes the stream that page opened.
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
            if ctx.state.get("progress") is None:
                stream = _pull_only(open_stream(bridge, ctx.input.key, ctx.input.partition, adapter))
            else:
                stream = ctx.input.current_stream(bridge)
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
    """Rescan one partition per attempt and wait while a retry falls due.

    The driver owns due-identity selection, conflict exclusion and baseline
    fallback. The step waits only while some failure the rescan retries is due
    within ``reconcile_seconds``. One that backs off further, or waits on a
    conflict, stays on its link for a later cycle and the step finishes reporting
    it, so a permanent refusal never holds the bridge's one cycle open. A
    baseline's persisted phase survives lost workflow settlement; continuation
    carries the partition index and progress digest, never a cursor.
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
        current = ctx.input.current_streams(bridge)
        wake = ctx.now + timedelta(seconds=ctx.config.reconcile_seconds)
        if any(row.links.rescan(limit=1, due_by=wake) for row in current):
            return ctx.wait(until=wake, state={"rescan_index": 0})
        return ctx.done(StreamStageOutput.for_streams(current, counts={"streams": len(streams)}))


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
    """Fence transport and classify infrastructure failures for native retries.

    A host that does not resolve is retried too: a resolver that is briefly offline
    answers "no such host" on some platforms (macOS), and a host that truly has no
    address still fails once the step's attempts run out.
    """
    ctx.heartbeat()
    ctx.begin_effect()
    try:
        with closing(bridge.backend) as adapter:
            yield adapter
    except AddressResolutionError as error:
        raise Retryable("The bridge host could not be resolved.") from error
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
