"""Execution resources, monitor-readable authoring and manager-backed actions."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import F, Prefetch
from rebac import system_context
from rebac.resources import model_for_resource_type
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.queryset import run_type_get_queryset

from angee.base.identity import public_id_for
from angee.base.impl import resolve_all_impl_classes
from angee.base.models import record_display_label
from angee.base.refs import canonical_record_model, canonical_record_target
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.decisions.schema import DecisionType
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    authorized_action_target,
    authorized_permission_target,
)
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import AngeeHasuraWriteBackend, declared_hasura_resource_fields, hasura_model_resource
from angee.graphql.ids import PublicID, optional_public_id, require_instance_for_id
from angee.graphql.impl import ImplChoice
from angee.graphql.node import AngeeNode
from angee.graphql.relations import (
    RecordReferenceNode,
    actor_scoped_to_many,
    actor_scoped_to_one,
    with_record_reference_access,
)
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_public_id
from angee.iam.permissions import request_from_info
from angee.iam.schema import UserType
from angee.workflows.definition import Body, Definition, Issue
from angee.workflows.models import RunGraphNode
from angee.workflows.states import RunOrigin, StepRunStatus, WaitingKind
from angee.workflows.steps import Step
from angee.workflows.triggers import TriggerGrantTarget

strawberry.enum(cast(Any, RunOrigin))
strawberry.enum(cast(Any, StepRunStatus))
strawberry.enum(cast(Any, WaitingKind))

Workflow = apps.get_model("workflows", "Workflow")
WorkflowVersion = apps.get_model("workflows", "WorkflowVersion")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
StepAttempt = apps.get_model("workflows", "StepAttempt")
StepRecord = apps.get_model("workflows", "StepRecord")
StepWatch = apps.get_model("workflows", "StepWatch")
Trigger = apps.get_model("workflows", "Trigger")
TriggerEvent = apps.get_model("workflows", "TriggerEvent")
Decision = apps.get_model("decisions", "Decision")
_RUN_POLICY_VERSION = Prefetch(
    "version",
    queryset=system_queryset(WorkflowVersion).only("document"),
    to_attr="policy_version",
)
_STEP_POLICY_VERSION = Prefetch(
    "run__version",
    queryset=system_queryset(WorkflowVersion).only("document"),
    to_attr="policy_version",
)


@strawberry_django.type(Workflow)
class WorkflowType(AngeeNode):
    """Workflow identity with draft fields governed by native REBAC redaction."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["name"])
    key: auto
    name: auto
    description: auto
    subject_model: auto
    permissions = permissions_field(("monitor", "write"))
    draft: JSON | None
    draft_revision: int | None
    layout: JSON | None

    published: WorkflowVersionType | None = actor_scoped_to_one("published")


@strawberry_django.type(WorkflowVersion)
class WorkflowVersionType(AngeeNode):
    """The immutable graph selected by an admitted run."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["number"])
    number: auto
    document: JSON
    content_hash: auto
    created_at: auto
    published_by: UserType | None = actor_scoped_to_one("published_by")
    workflow: WorkflowType | None = actor_scoped_to_one("workflow")


@strawberry_django.type(WorkflowRun)
class WorkflowRunType(RecordReferenceNode):
    """Execution state, admission and result visible through the run's policy."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name,
        annotate={"_workflow_name": F("version__workflow__name")}, only=["created_at", "version_id"],
    )
    version: WorkflowVersionType | None = actor_scoped_to_one("version")
    parent_step: StepRunType | None = actor_scoped_to_one("parent_step")
    reprocess_of: WorkflowRunType | None = actor_scoped_to_one("reprocess_of")
    trigger_event: TriggerEventType | None = actor_scoped_to_one("trigger_event")
    records: list[StepRecordType] = actor_scoped_to_many("records")
    step_runs: list[StepRunType] = actor_scoped_to_many("step_runs")
    run_as: UserType | None = actor_scoped_to_one("run_as")
    status: auto
    origin: RunOrigin
    @strawberry_django.field(only=["input", "version_id"])
    def input(self, info: strawberry.Info) -> JSON:
        """Keep non-reference input while checking marked sources at read time."""
        run = cast(Any, self)
        evidence = read_scoped_queryset(StepRecord, request_from_info(info).user)
        readable: set[tuple[str, str]] = set()
        for row in with_record_reference_access(evidence.filter(run_id=run.pk)):
            if row._angee_record_readable:
                readable.add((row.record_model_label.lower(), row.record_public_id))
        return run.policy_version.definition.redacted_input(run.input, readable)
    output: JSON
    outcome: auto
    outcome_label: str = strawberry_django.field(
        only=["outcome", "version_id"], prefetch_related=[_RUN_POLICY_VERSION],
    )
    error: str | None
    failure_reason: str | None = strawberry_django.field(only=["outcome", "error", "output"])
    request_key: auto
    created_at: auto
    updated_at: auto
    finished_at: auto
    stopped_at: auto

    @strawberry_django.field(only=["version_id", "status", "stopped_at"], prefetch_related=[_RUN_POLICY_VERSION])
    def graph(self, info: strawberry.Info) -> WorkflowRunGraph:
        """Read one run's bounded graph; list selections cost about five queries per row."""
        return cast(WorkflowRunGraph, cast(Any, self).graph(request_from_info(info).user))

    @strawberry_django.field(only=["status", "stopped_at"])
    def can_cancel(self, info: strawberry.Info) -> bool:
        """Project the model's viewer-specific cancellation predicate."""
        return bool(cast(Any, self).can_cancel(request_from_info(info).user))

    @strawberry_django.field(only=["status", "version_id"])
    def can_reprocess(self, info: strawberry.Info) -> bool:
        """Project the model's viewer-specific replacement admission predicate."""
        return bool(cast(Any, self).can_reprocess(request_from_info(info).user))

    subject_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["subject_content_type_id", "subject_object_id"],
    )
    subject_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["subject_content_type_id", "subject_object_id"],
    )


@strawberry.type
class StepNoteType:
    """Reader-facing outcome notes, validated by the step context."""

    tone: str
    message: str


@strawberry_django.type(StepRun)
class StepRunType(AngeeNode):
    """A node's retained execution and wait state; transitions use actions."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["node_key", "map_index"])
    run: WorkflowRunType | None = actor_scoped_to_one("run")
    child_runs: list[WorkflowRunType] = actor_scoped_to_many("child_runs")
    awaited_run: WorkflowRunType | None = actor_scoped_to_one("awaited_run")
    decision: DecisionType | None = actor_scoped_to_one("decision")
    attempts: list[StepAttemptType] = actor_scoped_to_many("attempts")
    records: list[StepRecordType] = actor_scoped_to_many("records")
    watches: list[StepWatchType] = actor_scoped_to_many("watches")
    node_key: auto
    node_label: str = strawberry_django.field(only=["node_key", "run_id"], prefetch_related=[_STEP_POLICY_VERSION])
    rank: auto
    map_index: auto
    is_mapped: bool = strawberry_django.field(only=["node_key"])
    is_map: bool = strawberry_django.field(only=["node_key", "run_id"], prefetch_related=[_STEP_POLICY_VERSION])
    map_total: int = strawberry_django.field(
        only=["input", "node_key", "run_id"], prefetch_related=[_STEP_POLICY_VERSION],
    )
    map_settled: int = strawberry_django.field(annotate={"_map_settled": StepRun.map_settled_expression()})
    status: auto
    waiting_kind: auto
    wait_reason: auto
    attempt: auto
    page_index: auto
    retries: auto
    dispatches: auto
    deadline_at: auto
    wake_at: auto
    dispatched_at: auto
    input: JSON
    output: JSON
    failure_reason: str | None = strawberry_django.field(only=["status", "output"])
    outcome: auto
    outcome_label: str = strawberry_django.field(
        only=["node_key", "outcome", "run_id"], prefetch_related=[_STEP_POLICY_VERSION],
    )
    state: JSON
    hold: str | None = strawberry_django.field(annotate={"_hold": StepRun.hold_expression()})
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["notes"])
    def notes(self) -> list[StepNoteType]:
        """Expose the note's small contract rather than untyped JSON."""
        return [StepNoteType(**note) for note in cast(Any, self).notes]

    @strawberry_django.field(only=["node_key", "run_id"])
    def map_steps(self, info: strawberry.Info) -> list[StepRunType]:
        """Map item execution stays beneath its authored parent in the timeline."""
        return cast(Any, self).map_rows().with_actor(request_from_info(info).user).order_by("map_index")

    @strawberry_django.field(only=["status", "waiting_kind", "node_key", "run_id"])
    def can_retry(self, info: strawberry.Info) -> bool:
        """Project the same model rule checked by the retry action."""
        return bool(cast(Any, self).can_retry(request_from_info(info).user))

    @strawberry_django.field(only=["node_key", "page_index", "run_id"])
    def requires_duplicate_acknowledgement(self) -> bool:
        """Read the step owner's current-page effect marker predicate."""
        return bool(cast(Any, self).requires_duplicate_acknowledgement)





@strawberry_django.type(StepAttempt)
class StepAttemptType(AngeeNode):
    """One claim's timing, effect marker, failure and operator acknowledgment."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["number"])
    step_run: StepRunType | None = actor_scoped_to_one("step_run")
    number: auto
    page_index: auto
    started_at: auto
    finished_at: auto
    result: auto
    error: str | None
    stacktrace: str | None
    effect_started_at: auto

    @strawberry_django.field(only=["acknowledged_by_id"])
    def acknowledged_by(self) -> PublicID | None:
        """Return the operator's public identity without exposing a user row."""
        return optional_public_id(user_public_id(cast(Any, self).acknowledged_by_id))


@strawberry.type
class WorkflowRunGraphOutcome:
    """A frozen outcome label attached to a graph port."""

    outcome: str
    label: str


@strawberry.type
class WorkflowRunGraphStatusCount:
    """Exact item cardinality in one execution state."""

    status: StepRunStatus
    count: int


@strawberry.type
class WorkflowRunGraphNode:
    """Published topology composed with one summary row and complete item counts."""

    step_run: StepRunType | None
    item_counts: list[WorkflowRunGraphStatusCount]
    item_attempts: int
    plan: str

    @strawberry.field
    def key(self) -> str:
        """Expose the definition owner's node identity."""
        return cast(RunGraphNode, self).node.key

    @strawberry.field
    def label(self) -> str:
        """Expose the published node label."""
        return cast(RunGraphNode, self).node.label

    @strawberry.field
    def step_label(self) -> str:
        """Expose the definition owner's implementation label."""
        return cast(RunGraphNode, self).node.step_label

    @strawberry.field
    def rank(self) -> int:
        """Expose deterministic execution order."""
        return cast(RunGraphNode, self).node.rank

    @strawberry.field
    def body_key(self) -> str | None:
        """Identify the optional item execution node."""
        return cast(RunGraphNode, self).node.body_key

    @strawberry.field
    def outcomes(self) -> list[WorkflowRunGraphOutcome]:
        """Expose the definition owner's named ports as a typed collection."""
        return [WorkflowRunGraphOutcome(outcome=key, label=label)
                for key, label in cast(RunGraphNode, self).node.outcomes.items()]


@strawberry.type
class WorkflowRunGraphEdge:
    """One published route with its retained execution evidence."""

    source: str
    outcome: str
    target: str
    taken: bool


@strawberry.type
class WorkflowRunGraph:
    """Frozen topology with actor-scoped execution resources and item summaries."""

    nodes: list[WorkflowRunGraphNode]
    edges: list[WorkflowRunGraphEdge]


@strawberry_django.type(StepWatch)
class StepWatchType(RecordReferenceNode):
    """A waiting step's reference, visible through its execution read policy."""

    step_run: StepRunType | None = actor_scoped_to_one("step_run")

    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


@strawberry_django.type(StepRecord)
class StepRecordType(RecordReferenceNode):
    """A step's labeled reference to a record, governed by its execution policy."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["label"])
    run: WorkflowRunType | None = actor_scoped_to_one("run")
    step_run: StepRunType | None = actor_scoped_to_one("step_run")
    operation: auto
    label: auto
    created_at: auto

    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


@strawberry.type
class TriggerGrantType:
    """One direct source grant held by a workflow principal."""

    resource_type: str
    resource_id: str
    relation: str
    relation_label: str
    target_kind: str
    target_label: str | None


@strawberry.type
class TriggerEnablePreviewType:
    """The source grants and workflow monitor readers disclosed before enabling."""

    grants: list[str]
    run_readers: list[str]


@strawberry_django.type(Trigger)
class TriggerType(AngeeNode):
    """A workflow's editable event admission policy and server-owned activation."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["source", "model_label"])
    workflow: WorkflowType | None = actor_scoped_to_one("workflow")
    source: auto
    model_label: auto
    source_model: str = strawberry_django.field(only=["source", "model_label"])
    condition: JSON
    enabled: auto
    disabled_reason: auto

    @strawberry_django.field
    def enable_preview(self, info: strawberry.Info) -> TriggerEnablePreviewType | None:
        """Show expected grants and inherited run readers only to eligible enablers."""
        preview = Trigger.objects.enable_preview(cast(Any, self), actor=request_from_info(info).user)
        return (TriggerEnablePreviewType(grants=list(preview.grants), run_readers=list(preview.run_readers))
                if preview is not None else None)

    @strawberry_django.field
    def grants(self, info: strawberry.Info) -> list[TriggerGrantType]:
        """List live direct tuples; reveal a target label only through readable rows."""
        actor = request_from_info(info).user
        rows = cast(Any, self).granted_relationships(actor=actor)
        targets = [TriggerGrantTarget.from_stored({
            "resource_type": str(row.resource_type), "resource_id": str(row.resource_id),
            "relation": str(row.relation),
        }) for row in rows]
        labels: dict[tuple[str, str], str] = {}
        for resource_type in sorted({str(row.resource_type) for row in rows}):
            model = model_for_resource_type(resource_type)
            if model is None or not model._meta.managed:
                continue
            visible = read_scoped_queryset(model, actor)
            ids = [str(row.resource_id) for row in rows if row.resource_type == resource_type]
            for target in visible.filter(pk__in=ids):
                labels[(resource_type, str(target.pk))] = record_display_label(target)
        return [
            TriggerGrantType(
                resource_type=target.resource.resource_type, resource_id=str(target.resource.resource_id),
                relation=target.relation, relation_label=target.relation_label(),
                target_kind=target.target_kind(),
                target_label=labels.get((target.resource.resource_type, str(target.resource.resource_id))),
            )
            for target in targets
        ]

    @strawberry_django.field
    def can_edit(self, info: strawberry.Info) -> bool:
        """Expose the permission owner's current write decision to authoring forms."""
        return bool(cast(Any, self).with_actor(request_from_info(info).user).has_access("write"))


@strawberry_django.type(TriggerEvent)
class TriggerEventType(RecordReferenceNode):
    """Durable admission evidence with a started run until that run is pruned."""

    trigger: TriggerType | None = actor_scoped_to_one("trigger")
    started_run: WorkflowRunType | None = actor_scoped_to_one("started_run")
    changed_at: auto
    evaluated_at: auto
    admitted_at: auto
    rejection: auto

    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["record_content_type_id", "record_object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["record_content_type_id", "record_object_id"],
    )


_WORKFLOW_RESOURCE = hasura_model_resource(
    WorkflowType,
    model=Workflow,
    filterable=["id", "key", "subject_model"],
    sortable=["key", "name", "created_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_VERSION_RESOURCE = hasura_model_resource(
    WorkflowVersionType,
    model=WorkflowVersion,
    filterable=["id", "workflow", "number"],
    sortable=["number", "created_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_RUN_RESOURCE = hasura_model_resource(
    WorkflowRunType,
    model=WorkflowRun,
    filterable=[
        "id",
        "version",
        "version__workflow",
        "version__workflow__key",
        "parent_step",
        "parent_step__run",
        "run_as",
        "status",
        "origin",
        "outcome",
        "reprocess_of",
        "trigger_event",
        "created_at",
        "finished_at",
    ],
    record_ref_filters=("subject_model", "subject_id"),
    record_ref_requires_read=True,
    sortable=["created_at", "updated_at", "finished_at", "status"],
    aggregatable=["id"],
    groupable=["status", "origin", "outcome", "version__workflow", "version__workflow__name"],
    insert=False,
    update=False,
    delete=False,
)
_STEP_RESOURCE = hasura_model_resource(
    StepRunType,
    model=StepRun,
    filterable=[
        "id",
        "run",
        "decision",
        "awaited_run",
        "node_key",
        "map_index",
        "status",
        "waiting_kind",
        "hold",
        "outcome",
    ],
    sortable=["rank", "created_at", "node_key", "map_index", "deadline_at", "wake_at"],
    aggregatable=["id"],
    groupable=["status", "waiting_kind"],
    filter_expressions={"hold": StepRun.hold_expression()},
    insert=False,
    update=False,
    delete=False,
)
_ATTEMPT_RESOURCE = hasura_model_resource(
    StepAttemptType,
    model=StepAttempt,
    filterable=["id", "step_run", "number", "result", "acknowledged_by"],
    sortable=["number", "started_at", "finished_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_RECORD_RESOURCE = hasura_model_resource(
    StepRecordType,
    model=StepRecord,
    filterable=["id", "run", "step_run", "operation", "label"],
    record_ref_filters=("record_model", "record_id"),
    record_ref_requires_read=True,
    sortable=["created_at", "label"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_WATCH_RESOURCE = hasura_model_resource(
    StepWatchType,
    model=StepWatch,
    filterable=["id", "step_run"],
    sortable=["id"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)
_TRIGGER_INSERT = (
    "workflow",
    "source",
    "model_label",
    "condition",
    *declared_hasura_resource_fields(Trigger, "hasura_insertable_fields"),
)
_TRIGGER_UPDATE = (
    "source",
    "model_label",
    "condition",
    *declared_hasura_resource_fields(Trigger, "hasura_updatable_fields"),
)
_TRIGGER_RESOURCE = hasura_model_resource(
    TriggerType,
    model=Trigger,
    filterable=[
        "id",
        "workflow",
        "source",
        "model_label",
        "enabled",
        *declared_hasura_resource_fields(Trigger, "hasura_filterable_fields"),
    ],
    sortable=["created_at", "updated_at"],
    aggregatable=["id"],
    insertable=_TRIGGER_INSERT,
    updatable=_TRIGGER_UPDATE,
    write_backend=AngeeHasuraWriteBackend(
        Trigger,
        public_id_fields=tuple(
            name
            for name in dict.fromkeys((*_TRIGGER_INSERT, *_TRIGGER_UPDATE))
            if Trigger._meta.get_field(name).is_relation
        ),
    ),
)
_TRIGGER_EVENT_RESOURCE = hasura_model_resource(
    TriggerEventType,
    model=TriggerEvent,
    filterable=["id", "trigger", "started_run", "admitted_at"],
    record_ref_filters=("record_model", "record_id"),
    record_ref_requires_read=True,
    sortable=["changed_at", "evaluated_at", "admitted_at"],
    aggregatable=["id"],
    insert=False,
    update=False,
    delete=False,
)


@strawberry.type
class WorkflowActionMutation:
    """Operator requests whose authorization and transitions belong to managers."""

    @strawberry.mutation
    @action_guard("Enable trigger failed.")
    def enable_workflow_trigger(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Enable through its policy owner as the requesting workflow author."""
        trigger = authorized_action_target(info, Trigger, id, "write")
        Trigger.objects.enable(trigger, actor=request_from_info(info).user)
        return ActionResult(ok=True, message="Trigger enabled.", id=trigger.sqid)

    @strawberry.mutation
    @action_guard("Disable trigger failed.")
    def disable_workflow_trigger(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Stop capturing future changes through the trigger lifecycle owner."""
        trigger = authorized_action_target(info, Trigger, id, "write")
        Trigger.objects.disable(trigger, actor=request_from_info(info).user)
        return ActionResult(ok=True, message="Trigger disabled.", id=trigger.sqid)

    @strawberry.mutation
    @action_guard("Revoke trigger grant failed.")
    def revoke_workflow_trigger_grant(
        self, info: strawberry.Info, id: PublicID, resource_type: str, resource_id: str, relation: str,
    ) -> ActionResult:
        """Revoke one source grant through the same workflow write gate as enable."""
        trigger = authorized_action_target(info, Trigger, id, "write")
        current = Trigger.objects.revoke_grant(
            trigger, actor=request_from_info(info).user,
            resource_type=resource_type, resource_id=resource_id, relation=relation,
        )
        return ActionResult(
            ok=True, message=current.disabled_reason or "Trigger grant revoked.", id=trigger.sqid,
        )

    @strawberry.mutation
    @action_guard("Cancel run failed.")
    def cancel_workflow_run(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Cancel one writable active run through the execution owner."""
        run = authorized_action_target(info, WorkflowRun, id, "write")
        result = WorkflowRun.objects.cancel(run, actor=request_from_info(info).user)
        return ActionResult(ok=True, message=result.message)

    @strawberry.mutation
    @action_guard("Reprocess run failed.")
    def reprocess_workflow_run(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Start a replacement for a terminal run as the requesting operator."""
        run = authorized_action_target(info, WorkflowRun, id, "write")
        replacement = WorkflowRun.objects.reprocess(run, actor=request_from_info(info).user)
        return ActionResult(ok=True, message="Run reprocessed.", id=replacement.sqid)

    @strawberry.mutation
    @action_guard("Retry step failed.")
    def retry_step(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Retry a visible step when the operator may write its owning run."""
        step_run = authorized_permission_target(info, StepRun, id, "read")
        authorized_action_target(info, WorkflowRun, step_run.run.sqid, "write")
        StepRun.objects.retry_step(step_run, actor=request_from_info(info).user)
        return ActionResult(ok=True, message="Step retried.")

    @strawberry.mutation
    @action_guard("Retry step failed.")
    def retry_step_accepting_duplicate(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Explicitly acknowledge a possible duplicate effect before retrying."""
        step_run = authorized_permission_target(info, StepRun, id, "read")
        authorized_action_target(info, WorkflowRun, step_run.run.sqid, "write")
        StepRun.objects.retry_step(step_run, actor=request_from_info(info).user, accept_duplicate=True)
        return ActionResult(ok=True, message="Step retried with duplicate risk acknowledged.")


@strawberry.type
class WorkflowStepChoice(ImplChoice):
    """Shared implementation metadata plus step-owned palette and outcome facts."""

    internal: bool
    outcomes: JSON

    @classmethod
    def from_step(cls, step: type[Step]) -> WorkflowStepChoice:
        """Build common metadata and workflow facts from one resolved class."""
        choice = step.choice()
        outcomes, _ = Definition.node_outcomes(
            step.key, Body(step=step.key, config=choice.defaults.get("config", {})), [], implementation=step,
        )
        return cls.from_choice(choice, internal=step.internal, outcomes=outcomes)


@strawberry.input
class WorkflowStepConfiguration:
    """One client node identity and the step configuration whose outcomes it needs."""

    node: str
    step: str
    config: JSON


@strawberry.experimental.pydantic.type(model=Issue, all_fields=True)
class WorkflowIssue:
    """Typed projection of the document owner's located diagnostic."""

    path: JSON


@strawberry.type
class WorkflowConfiguredOutcomes:
    """Configured outcomes associated with their stable client node identity."""

    node: str
    outcomes: JSON
    issues: list[WorkflowIssue]


@strawberry.type
class WorkflowStudioQuery:
    """Monitor-readable adapters over the rowless implementation and step owners."""

    @strawberry.field
    def workflow_step_choices(self, info: strawberry.Info, id: PublicID) -> list[WorkflowStepChoice]:
        """Offer registered steps, including internal metadata for retained nodes."""
        authorized_permission_target(info, Workflow, id, "monitor")
        return [WorkflowStepChoice.from_step(step) for step in resolve_all_impl_classes(Step)]

    @strawberry.field
    def workflow_step_outcomes(
        self,
        info: strawberry.Info,
        id: PublicID,
        configurations: list[WorkflowStepConfiguration],
    ) -> list[WorkflowConfiguredOutcomes]:
        """Project each unfinished node through actor-scoped resolution and its owner."""
        authorized_permission_target(info, Workflow, id, "monitor")
        return [
            WorkflowConfiguredOutcomes(
                node=node,
                outcomes=cast(JSON, outcomes),
                issues=cast(list[WorkflowIssue], issues),
            )
            for node, outcomes, issues in Workflow.objects.authoring_outcomes(
                [{"node": entry.node, "step": entry.step, "config": entry.config}
                 for entry in configurations],
                actor=request_from_info(info).user,
            )
        ]


@strawberry.type
class WorkflowDraftAcknowledgement:
    """Revision accepted by this save and its non-blocking diagnostics."""

    revision: int
    diagnostics: list[WorkflowIssue]


@strawberry.type
class WorkflowPublication:
    """Publication number and readable dependents returned by its owner."""

    number: int
    dependents: list[str]


@strawberry.type
class WorkflowStudioMutation:
    """Thin explicit authoring submissions; managers retain all write policy."""

    @strawberry.mutation
    def save_workflow_draft(
        self,
        info: strawberry.Info,
        id: PublicID,
        draft: JSON,
        layout: JSON,
        expected_revision: int,
        node_keys: JSON | None = None,
    ) -> WorkflowDraftAcknowledgement:
        """Save the authored document and layout against the observed revision."""
        workflow = authorized_permission_target(info, Workflow, id, "write")
        saved = Workflow.objects.save_draft(
            workflow,
            draft=draft,
            layout=layout,
            expected_revision=expected_revision,
            node_keys=node_keys,
            actor=request_from_info(info).user,
        )
        return WorkflowDraftAcknowledgement(
            revision=saved.revision,
            diagnostics=cast(list[WorkflowIssue], saved.issues),
        )

    @strawberry.mutation
    def publish_workflow(self, info: strawberry.Info, id: PublicID, expected_revision: int) -> WorkflowPublication:
        """Publish the saved revision through its publication owner."""
        workflow = authorized_permission_target(info, Workflow, id, "write")
        published = Workflow.objects.publish(
            workflow,
            expected_revision=expected_revision,
            actor=request_from_info(info).user,
        )
        return WorkflowPublication(number=published.version.number, dependents=list(published.dependents))


_RESOURCES = (
    _WORKFLOW_RESOURCE,
    _VERSION_RESOURCE,
    _RUN_RESOURCE,
    _STEP_RESOURCE,
    _ATTEMPT_RESOURCE,
    _RECORD_RESOURCE,
    _WATCH_RESOURCE,
    _TRIGGER_RESOURCE,
    _TRIGGER_EVENT_RESOURCE,
)

@strawberry.input
class TimelineRecordInput:
    model: str
    id: PublicID


@strawberry.type
class RecordTimelineType:
    record_model: str
    record_id: PublicID
    runs: list[WorkflowRunType]
    decisions: list[DecisionType]


@strawberry.type
class RecordTimelineSelectionType:
    records: list[RecordTimelineType]
    open_decision_count: int


@strawberry.type
class RecordTimelineQuery:
    @strawberry_django.field
    def record_timeline(self, info: strawberry.Info, records: list[TimelineRecordInput]) -> RecordTimelineSelectionType:
        """One read for a record or selection, through the record and run owners."""
        actor = request_from_info(info).user
        result = []
        concerned = []
        for reference in records:
            model = apps.get_model(reference.model)
            record = require_instance_for_id(model, str(reference.id), queryset=read_scoped_queryset(model, actor))
            target = canonical_record_target(record)
            concerned.append(record)
            result.append(RecordTimelineType(
                record_model=canonical_record_model(type(record))._meta.label,
                record_id=PublicID(public_id_for(canonical_record_model(type(record)), target.object_id)),
                runs=run_type_get_queryset(WorkflowRun.objects.with_actor(actor).about(record), WorkflowRunType, info),
                decisions=Decision.objects.with_actor(actor).open_for(record),
            ))
        with system_context(reason="workflows.timeline.selection_attention"):
            count = Decision.objects.open_for(*concerned).count()
        return RecordTimelineSelectionType(records=result, open_decision_count=count)

schemas = {
    "console": {
        "query": [WorkflowStudioQuery, RecordTimelineQuery, *(resource.query for resource in _RESOURCES)],
        "mutation": [WorkflowStudioMutation, WorkflowActionMutation, _TRIGGER_RESOURCE.mutation],
        "subscription": [changes(WorkflowRun, field="workflowRunChanged"), changes(StepRun, field="stepRunChanged")],
        "types": [
            RunOrigin,
            WorkflowType,
            WorkflowVersionType,
            WorkflowRunType,
            StepRunType,
            StepAttemptType,
            StepRecordType,
            StepWatchType,
            TriggerEnablePreviewType,
            TriggerType,
            TriggerEventType,
            *(type_ for resource in _RESOURCES for type_ in resource.types),
        ],
    },
}
"""Console-only execution reads and operator mutations composed by the schema owner."""
