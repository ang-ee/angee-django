"""Workflow execution and review composed over integrate's durable stream owners."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from rebac import system_context

from angee.base.db import get_write_alias, related_on
from angee.base.identity import instance_from_public_id, public_id_of
from angee.integrate.models import Bridge
from angee.integrate.records import DiscrepancyKind, DiscrepancyStatus
from angee.integrate.streams import advance_stream, begin_stream_cycle, open_stream
from angee.workflows.attempts import RecoveryMode
from angee.workflows.configs import WorkflowStepConfig
from angee.workflows.decision_actions import ReviewAction, ReviewRecordReference, build_decision_action
from angee.workflows.steps import GateStep, StepEffect, StepExecutionMode, StepImpl, StepResult, TransientStepError


class BridgeReference(BaseModel):
    """Exact concrete Bridge public identity, bound to the admitted run subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = Field(min_length=1)
    id: str = Field(min_length=1)


class StreamReference(BaseModel):
    """A backend's stream partition identity, independent of its current epoch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1)
    partition: str = ""


class StreamStageInput(StreamReference):
    """Immutable page parameters; the cursor is exclusively SyncStream-owned."""

    bridge: BridgeReference
    page_bound: int = Field(default=100, ge=1)


class StreamStageOutput(BaseModel):
    """Stage counts and durable stream/discrepancy evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    counts: dict[str, int]
    discrepancy_ids: list[str]
    evidence: list[ReviewRecordReference]


class CoverageInput(BaseModel):
    """The complete stream partition set whose coverage this cycle requires."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bridge: BridgeReference
    streams: list[StreamReference] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_streams(self) -> CoverageInput:
        """Reject duplicate identities instead of concealing an authoring mistake."""

        identities = [(stream.key, stream.partition) for stream in self.streams]
        if len(identities) != len(set(identities)):
            raise ValueError("Coverage streams must be distinct.")
        return self


class CoverageConfig(WorkflowStepConfig):
    """Bounded reconciliation timer for discrepancies resolved outside the run."""

    reconcile_seconds: int = Field(default=60, ge=1)


class BoundedStreamStage(StepImpl):
    """Advance one driver page outside the engine's finalization transaction.

    A crash after the page commits replays from SyncStream.cursor. Resume state
    retains stream correlation and the applied count from finalized pulses;
    completion publishes that cycle total. The separate page/finalization commits
    can omit a crashed pulse's count, but never reconstruct a cursor or reapply
    that page. Consumers may declare their concrete subject_declaration.
    """

    key = "integrate_stream"
    label = "Advance sync stream"
    category = "Activity"
    description = "Apply one bounded stream page and retain a continuation until exhausted."
    input_model = StreamStageInput
    output_model = StreamStageOutput
    config_model = WorkflowStepConfig
    execution_mode = StepExecutionMode.STANDARD
    effect = StepEffect.WRITE
    effect_description = "The integrate driver commits each page with its durable cursor."
    idempotent = True
    replay_mode = RecoveryMode.FRESH
    deterministic = False

    def run(self, step_run: Any, *, now: datetime, using: str | None = None) -> StepResult:
        """Resolve the admitted partition and delegate exactly one page to its owner."""

        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        value = self.validate_input(step_run.input)
        with system_context(reason="workflows_integrate.stream.resolve"):
            bridge = _bridge_for_step(step_run, value.bridge, using=alias)
        try:
            with (
                self.heartbeat_during(step_run, using=alias),
                closing(bridge.backend) as adapter,
                system_context(reason="workflows_integrate.stream"),
            ):
                streams = apps.get_model("integrate", "SyncStream").objects.db_manager(alias)
                stream = (
                    streams.current_for_bridge(bridge, value.key, using=alias).filter(partition=value.partition).first()
                )
                if stream is None:
                    definitions = [
                        definition
                        for definition in adapter.streams(using=alias)
                        if (definition.key, definition.partition) == (value.key, value.partition)
                    ]
                    if len(definitions) != 1:
                        raise ValidationError(
                            {"stream": "The backend must declare the requested partition exactly once."}
                        )
                    stream = open_stream(bridge, definitions[0], using=alias)
                # The page commits its timestamp with its cursor. Preparation
                # repeats safely before that first commit, including a crashed
                # first attempt, but never rescans a page this stage committed.
                # A retained reset wait also proves preparation: the new epoch
                # has no last_advanced_at until its first baseline page commits.
                if not step_run.resume_state and (
                    stream.last_advanced_at is None or stream.last_advanced_at < step_run.created_at
                ):
                    stream = begin_stream_cycle(stream, adapter, using=alias)
                page = advance_stream(stream, adapter, page_bound=value.page_bound, using=alias)
                cycle_items = step_run.resume_state.get("cycle_items", 0) + page.count
                if not page.exhausted:
                    return StepResult.wait(
                        until=now,
                        resume_state={
                            "stream": public_id_of(page.stream),
                            "generation": page.stream.generation,
                            "cycle_items": cycle_items,
                        },
                    )
                discrepancies = list(
                    apps.get_model("integrate", "SyncDiscrepancy")
                    .objects.db_manager(alias)
                    .filter(
                        stream=page.stream,
                        status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                    )
                    .order_by("pk")
                )
                return StepResult.done(
                    output=StreamStageOutput(
                        counts={"page_items": page.count, "cycle_items": cycle_items},
                        discrepancy_ids=[public_id_of(row) for row in discrepancies],
                        evidence=[_evidence(page.stream), *(_evidence(row) for row in discrepancies)],
                    ).model_dump(mode="json")
                )
        except (ValidationError, TransientStepError):
            raise
        except Exception as error:  # noqa: BLE001 -- driver quarantines semantic failures itself.
            raise TransientStepError(str(error) or type(error).__name__) from error


class CoverageGate(GateStep):
    """Refuse acceptance until every cycle stream's discrepancies are resolved.

    Conflict Decisions request review, never authorize automatic reconciliation.
    Their completion only rechecks coverage: the discrepancy remains authoritative
    until its own domain resolution calls SyncDiscrepancy.objects.resolve().
    """

    key = "integrate_coverage"
    label = "Verify sync coverage"
    category = "Control"
    description = "Review conflicts and wait for all required stream discrepancies to resolve."
    input_model = CoverageInput
    output_model = StreamStageOutput
    config_model = CoverageConfig
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    effect = StepEffect.WRITE
    effect_description = "Creates native workflow Decisions for unresolved stream conflicts."
    idempotent = True
    replay_mode = RecoveryMode.FRESH
    deterministic = False
    outcomes = ()

    def run(self, step_run: Any, *, now: datetime, using: str | None = None) -> StepResult:
        """Compose native gate admission, then keep acceptance tied to data truth."""

        alias = get_write_alias(type(step_run), using=using, instance=step_run)
        value = self.validate_input(step_run.input)
        with system_context(reason="workflows_integrate.coverage"):
            bridge = _bridge_for_step(step_run, value.bridge, using=alias)
            step_run.step = related_on(step_run, "step", using=alias)
            config = CoverageConfig.model_validate(step_run.step.config)
            manager = apps.get_model("integrate", "SyncStream").objects.db_manager(alias)
            streams = [
                manager.current_for_bridge(bridge, reference.key, using=alias).get(partition=reference.partition)
                for reference in value.streams
            ]
            discrepancies = list(
                apps.get_model("integrate", "SyncDiscrepancy")
                .objects.db_manager(alias)
                .filter(
                    stream__in=streams,
                    status__in=(DiscrepancyStatus.OPEN, DiscrepancyStatus.RETRY),
                )
                .order_by("pk")
            )
            if not discrepancies:
                return StepResult.done(
                    output=StreamStageOutput(
                        counts={"streams": len(streams)},
                        discrepancy_ids=[],
                        evidence=[_evidence(stream) for stream in streams],
                    ).model_dump(mode="json")
                )
            decisions = apps.get_model("workflows", "Decision").objects.db_manager(alias)
            reviewed = {
                payload.get("discrepancy")
                for payload in decisions.filter(step_run=step_run).values_list("payload", flat=True)
            }
            conflicts = [
                row
                for row in discrepancies
                if row.kind == DiscrepancyKind.CONFLICT and public_id_of(row) not in reviewed
            ]
            if conflicts:
                run = related_on(step_run, "run", using=alias)
                actor = run.admission_actor_subject()
                if actor is None:
                    raise ValidationError({"actor": "Coverage review requires the run's admitted actor."})
                slots = []
                for row in conflicts:
                    review = build_decision_action(
                        actions=(ReviewAction(value="recheck", label="Recheck coverage", verdict="COMPLETE"),),
                        payload={"discrepancy": public_id_of(row)},
                        references=_evidence(row),
                    )
                    slots.append(
                        {
                            "assignees": [str(actor)],
                            "payload": review.payload,
                            "decision_schema": review.decision_schema,
                            "target": {"model": row._meta.label_lower, "id": public_id_of(row)},
                        }
                    )
                return self.gate_result(
                    step_run,
                    config={
                        "action": "review-sync-conflict",
                        "policy": "all_done",
                        "resume": True,
                        "slots": slots,
                    },
                )
            return StepResult.wait(
                until=now + timedelta(seconds=config.reconcile_seconds),
                resume_state={"streams": [public_id_of(stream) for stream in streams]},
            )


def _bridge_for_step(step_run: Any, reference: BridgeReference, *, using: str) -> Any:
    """Resolve only the Bridge that was admitted as this run's subject."""

    run = related_on(step_run, "run", using=using)
    content_type = related_on(run, "subject_content_type", using=using)
    model = content_type.model_class() if content_type is not None else None
    if model is None or not issubclass(model, Bridge) or model._meta.label_lower != reference.model.lower():
        raise ValidationError({"bridge": "The stage bridge must be the workflow run's concrete Bridge subject."})
    bridge = instance_from_public_id(model, reference.id, queryset=model._base_manager.using(using).all())
    if bridge is None or bridge.pk != run.subject_object_id:
        raise ValidationError({"bridge": "The stage bridge must match the admitted workflow subject."})
    return bridge


def _evidence(record: Any) -> ReviewRecordReference:
    return ReviewRecordReference(model=record._meta.label_lower, id=public_id_of(record))
