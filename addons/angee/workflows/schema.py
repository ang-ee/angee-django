"""Read-only execution resources and manager-backed operator actions."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import F, Prefetch
from rebac.resources import model_for_resource_type
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.models import record_display_label
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.decisions.schema import DecisionGroupType
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    authorized_action_target,
    authorized_permission_target,
)
from angee.graphql.data import AngeeHasuraWriteBackend, declared_hasura_resource_fields, hasura_model_resource
from angee.graphql.ids import PublicID, optional_public_id
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
from angee.workflows.states import RunOrigin
from angee.workflows.triggers import TriggerGrantTarget

strawberry.enum(cast(Any, RunOrigin))

Workflow = apps.get_model("workflows", "Workflow")
WorkflowVersion = apps.get_model("workflows", "WorkflowVersion")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
WorkflowRunEvidence = apps.get_model("workflows", "WorkflowRunEvidence")
StepRun = apps.get_model("workflows", "StepRun")
StepAttempt = apps.get_model("workflows", "StepAttempt")
StepArtifact = apps.get_model("workflows", "StepArtifact")
StepWatch = apps.get_model("workflows", "StepWatch")
Trigger = apps.get_model("workflows", "Trigger")
TriggerEvent = apps.get_model("workflows", "TriggerEvent")
DecisionGroup = apps.get_model("decisions", "DecisionGroup")
Decision = apps.get_model("decisions", "Decision")
_RUN_POLICY_VERSION = Prefetch(
    "version", queryset=system_queryset(WorkflowVersion).only("document"), to_attr="policy_version",
)
_STEP_POLICY_VERSION = Prefetch(
    "run__version", queryset=system_queryset(WorkflowVersion).only("document"), to_attr="policy_version",
)


@strawberry_django.type(Workflow)
class WorkflowType(AngeeNode):
    """The identity of a run's workflow, without unpublished authoring fields."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["name"])
    key: auto
    name: auto
    description: auto
    subject_model: auto
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
    evidence: list[WorkflowRunEvidenceType] = actor_scoped_to_many("evidence")
    step_runs: list[StepRunType] = actor_scoped_to_many("step_runs")
    run_as: UserType | None = actor_scoped_to_one("run_as")
    status: auto
    origin: RunOrigin
    @strawberry_django.field(only=["input", "version_id"])
    def input(self, info: strawberry.Info) -> JSON:
        """Keep non-reference input while checking marked sources at read time."""
        run = cast(Any, self)
        evidence = read_scoped_queryset(WorkflowRunEvidence, request_from_info(info).user)
        readable: set[tuple[str, str]] = set()
        if evidence is not None:
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

    @strawberry_django.field(only=["status"])
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


@strawberry_django.type(WorkflowRunEvidence)
class WorkflowRunEvidenceType(RecordReferenceNode):
    """A retained run input reference, redacted when its source is unreadable."""

    run: WorkflowRunType | None = actor_scoped_to_one("run")
    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


@strawberry_django.type(StepRun)
class StepRunType(AngeeNode):
    """A node's retained execution and wait state; transitions use actions."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["node_key", "map_index"])
    run: WorkflowRunType | None = actor_scoped_to_one("run")
    child_runs: list[WorkflowRunType] = actor_scoped_to_many("child_runs")
    awaited_run: WorkflowRunType | None = actor_scoped_to_one("awaited_run")
    decision_group: DecisionGroupType | None = actor_scoped_to_one("decision_group")
    attempts: list[StepAttemptType] = actor_scoped_to_many("attempts")
    artifacts: list[StepArtifactType] = actor_scoped_to_many("artifacts")
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
    retries: auto
    dispatches: auto
    deadline_at: auto
    wake_at: auto
    dispatched_at: auto
    input: JSON
    output: JSON
    outcome: auto
    outcome_label: str = strawberry_django.field(
        only=["node_key", "outcome", "run_id"], prefetch_related=[_STEP_POLICY_VERSION],
    )
    state: JSON
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["status", "waiting_kind", "node_key", "run_id"])
    def can_retry(self, info: strawberry.Info) -> bool:
        """Project the same model rule checked by the retry action."""
        return bool(cast(Any, self).can_retry(request_from_info(info).user))

    @strawberry_django.field(only=["node_key", "page_index", "run_id"])
    def requires_duplicate_acknowledgement(self) -> bool:
        """Read the step owner's current-page effect marker predicate."""
        return bool(cast(Any, self).requires_duplicate_acknowledgement)


@strawberry_django.type(DecisionGroup, name="DecisionGroupType", extend=True)
class DecisionGroupWorkflowExtension:
    """Expose the unique waiting execution through its own read permission."""

    step_run: StepRunType | None = actor_scoped_to_one("step_run", reverse=True)


@strawberry_django.type(Decision, name="DecisionType", extend=True)
class DecisionWorkflowExtension:
    """Project execution display fields through every related owner's read scope."""

    workflow_name: str | None
    node_key: str | None


@strawberry_django.type(StepAttempt)
class StepAttemptType(AngeeNode):
    """One claim's timing, effect marker, failure and operator acknowledgment."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["number"])
    step_run: StepRunType | None = actor_scoped_to_one("step_run")
    number: auto
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


@strawberry_django.type(StepArtifact)
class StepArtifactType(RecordReferenceNode):
    """A step's labeled reference to a record, governed by its execution policy."""

    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["label"])
    step_run: StepRunType | None = actor_scoped_to_one("step_run")
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
            if visible is None:
                continue
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
    started_run: WorkflowRunType | None = actor_scoped_to_one("started_run", reverse=True)
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
    WorkflowType, model=Workflow, filterable=["id", "key", "subject_model"],
    sortable=["key", "name", "created_at"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)
_VERSION_RESOURCE = hasura_model_resource(
    WorkflowVersionType, model=WorkflowVersion, filterable=["id", "workflow", "number"],
    sortable=["number", "created_at"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)
_RUN_RESOURCE = hasura_model_resource(
    WorkflowRunType, model=WorkflowRun,
    filterable=["id", "version", "version__workflow", "version__workflow__key", "parent_step", "parent_step__run",
                "run_as", "status", "origin", "outcome", "reprocess_of", "trigger_event",
                "created_at", "finished_at"],
    record_ref_filters=("subject_model", "subject_id"), record_ref_requires_read=True,
    sortable=["created_at", "updated_at", "finished_at", "status"],
    aggregatable=["id"], groupable=["status", "origin", "outcome", "version__workflow", "version__workflow__name"],
    insert=False, update=False, delete=False,
)
_RUN_EVIDENCE_RESOURCE = hasura_model_resource(
    WorkflowRunEvidenceType, model=WorkflowRunEvidence, filterable=["id", "run"],
    record_ref_filters=("record_model", "record_id"), record_ref_requires_read=True,
    sortable=["id"], aggregatable=["id"], insert=False, update=False, delete=False,
)
_STEP_RESOURCE = hasura_model_resource(
    StepRunType, model=StepRun,
    filterable=["id", "run", "decision_group", "awaited_run", "node_key", "map_index",
                "status", "waiting_kind", "outcome"],
    sortable=["rank", "created_at", "node_key", "map_index", "deadline_at", "wake_at"],
    aggregatable=["id"], groupable=["status", "waiting_kind"],
    insert=False, update=False, delete=False,
)
_ATTEMPT_RESOURCE = hasura_model_resource(
    StepAttemptType, model=StepAttempt,
    filterable=["id", "step_run", "number", "result", "acknowledged_by"],
    sortable=["number", "started_at", "finished_at"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)
_ARTIFACT_RESOURCE = hasura_model_resource(
    StepArtifactType, model=StepArtifact,
    filterable=["id", "step_run", "label"], sortable=["created_at", "label"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)
_WATCH_RESOURCE = hasura_model_resource(
    StepWatchType, model=StepWatch, filterable=["id", "step_run"], sortable=["id"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)
_TRIGGER_INSERT = ("workflow", "source", "model_label", "condition",
                   *declared_hasura_resource_fields(Trigger, "hasura_insertable_fields"))
_TRIGGER_UPDATE = ("source", "model_label", "condition",
                   *declared_hasura_resource_fields(Trigger, "hasura_updatable_fields"))
_TRIGGER_RESOURCE = hasura_model_resource(
    TriggerType, model=Trigger,
    filterable=["id", "workflow", "source", "model_label", "enabled",
                *declared_hasura_resource_fields(Trigger, "hasura_filterable_fields")],
    sortable=["created_at", "updated_at"], aggregatable=["id"],
    insertable=_TRIGGER_INSERT, updatable=_TRIGGER_UPDATE,
    write_backend=AngeeHasuraWriteBackend(Trigger, public_id_fields=tuple(
        name for name in dict.fromkeys((*_TRIGGER_INSERT, *_TRIGGER_UPDATE))
        if Trigger._meta.get_field(name).is_relation
    )),
)
_TRIGGER_EVENT_RESOURCE = hasura_model_resource(
    TriggerEventType, model=TriggerEvent, filterable=["id", "trigger", "started_run", "admitted_at"],
    record_ref_filters=("record_model", "record_id"),
    record_ref_requires_read=True,
    sortable=["changed_at", "evaluated_at", "admitted_at"], aggregatable=["id"],
    insert=False, update=False, delete=False,
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


_RESOURCES = (
    _WORKFLOW_RESOURCE, _VERSION_RESOURCE, _RUN_RESOURCE, _RUN_EVIDENCE_RESOURCE,
    _STEP_RESOURCE, _ATTEMPT_RESOURCE, _ARTIFACT_RESOURCE, _WATCH_RESOURCE,
    _TRIGGER_RESOURCE, _TRIGGER_EVENT_RESOURCE,
)
schemas = {
    "console": {
        "query": [resource.query for resource in _RESOURCES],
        "mutation": [WorkflowActionMutation, _TRIGGER_RESOURCE.mutation],
        "subscription": [changes(WorkflowRun, field="workflowRunChanged")],
        "type_extensions": [DecisionGroupWorkflowExtension, DecisionWorkflowExtension],
        "types": [
            RunOrigin,
            WorkflowType, WorkflowVersionType, WorkflowRunType, WorkflowRunEvidenceType,
            StepRunType, StepAttemptType, StepArtifactType, StepWatchType,
            TriggerEnablePreviewType, TriggerType, TriggerEventType,
            *(type_ for resource in _RESOURCES for type_ in resource.types),
        ],
    },
}
"""Console-only execution reads and operator mutations composed by the schema owner."""
