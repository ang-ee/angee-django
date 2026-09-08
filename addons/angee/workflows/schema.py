"""GraphQL schema contributions for the workflows addon."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import public_data_id_field
from angee.base.scoping import read_scoped_queryset
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    action_target,
    authorized_action_target,
    resolve_action_target,
)
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, instance_for_id, to_public_id
from angee.graphql.impl import ImplChoice as GraphQLImplChoice
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import session_user
from angee.workflows import engine
from angee.workflows.attempts import JsonPresence
from angee.workflows.data_contracts import DataContract, FlatDataContractEdge, FlatDataContractNode
from angee.workflows.definitions import (
    DefinitionEdit,
    DefinitionEditError,
    DefinitionReadinessError,
    EdgeCreate,
    EdgeDelete,
    EdgePatch,
    EndpointRef,
    NodeCreate,
    NodeDelete,
    NodePatch,
    StaleDefinitionError,
)
from angee.workflows.graph import GraphDiagnostic, GraphIdentity, GraphLocation
from angee.workflows.models import TriggerKind
from angee.workflows.steps import StepEffect, StepImpl, StepOperation
from angee.workflows.trigger_declarations import (
    schedule_draft_preview,
    trigger_config_schema,
    trigger_kind_names,
)

Workflow = apps.get_model("workflows", "Workflow")
Step = apps.get_model("workflows", "Step")
Edge = apps.get_model("workflows", "Edge")
Trigger = apps.get_model("workflows", "Trigger")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
Decision = apps.get_model("workflows", "Decision")

_PROJECTED_STEP_ID = "_workflows_step_id"
_PROJECTED_STEP_WORKFLOW_NAME = "_workflows_step_workflow_name"
_PROJECTED_RUN_WORKFLOW_NAME = "_workflows_run_workflow_name"
_PROJECTED_STEP_NAME = "_workflows_step_name"
_PROJECTED_STEP_KEY = "_workflows_step_key"
_PROJECTED_SYSTEM_KIND = "_workflows_system_kind"
_PROJECTED_STEP_RUN_ID = "_workflows_step_run_id"


def _decision_schema(root: Any) -> JSON | None:
    """Return the model-owned JSON form schema without exposing its journal."""

    return cast(JSON | None, cast(Any, root).form_schema)


def _decision_schema_field() -> Any:
    """Return the optimized decision form-schema GraphQL field."""

    return strawberry_django.field(
        resolver=_decision_schema,
        only=["id"],
        annotate=cast(Any, Decision).form_schema_annotation(),
    )


@strawberry.enum
class DecisionVerb(Enum):
    """Public verbs accepted by decision resolution mutations."""

    COMPLETE = "complete"
    REJECT = "reject"
    ESCALATE = "escalate"


WorkflowStepEffectEnum = strawberry.enum(StepEffect, name="WorkflowStepEffect")


@strawberry.type
class WorkflowStepOutcome:
    """One labeled routing outcome declared by a workflow operation."""

    key: str
    label: str
    description: str


@strawberry.type
class WorkflowDataContractNode:
    id: int
    kind: str
    json_type: str | None
    title: str | None
    description: str | None
    nullable: bool

    @classmethod
    def from_node(cls, node: FlatDataContractNode) -> "WorkflowDataContractNode":
        return cls(
            id=node.id,
            kind=node.kind,
            json_type=node.json_type,
            title=node.title,
            description=node.description,
            nullable=node.nullable,
        )


@strawberry.type
class WorkflowDataContractEdge:
    parent_node_id: int
    child_node_id: int
    kind: str
    key: str | None

    @classmethod
    def from_edge(cls, edge: FlatDataContractEdge) -> "WorkflowDataContractEdge":
        return cls(
            parent_node_id=edge.parent_node_id,
            child_node_id=edge.child_node_id,
            kind=edge.kind,
            key=edge.key,
        )


@strawberry.type
class WorkflowDataContract:
    raw_schema: JSON | None
    root_node_id: int
    nodes: list[WorkflowDataContractNode]
    edges: list[WorkflowDataContractEdge]

    @classmethod
    def from_contract(cls, contract: DataContract) -> "WorkflowDataContract":
        catalogue = contract.flat_catalogue()
        return cls(
            raw_schema=cast(JSON | None, contract.raw_schema),
            root_node_id=catalogue.root_node_id,
            nodes=[WorkflowDataContractNode.from_node(node) for node in catalogue.nodes],
            edges=[WorkflowDataContractEdge.from_edge(edge) for edge in catalogue.edges],
        )


@strawberry.type
class WorkflowStepOperation(GraphQLImplChoice):
    """Workflow-owned authoring contract for one registered step implementation."""

    description: str
    selectable: bool
    input_schema: JSON | None
    output_schema: JSON | None
    input_contract: WorkflowDataContract
    output_contract: WorkflowDataContract
    outcomes: list[WorkflowStepOutcome]
    effect: StepEffect
    effect_description: str
    idempotent: bool | None
    subject_declaration: str

    @classmethod
    def from_operation(cls, operation: StepOperation) -> "WorkflowStepOperation":
        """Project the owning step declaration while reusing generic choice metadata."""

        return cls(
            key=operation.choice.key,
            label=operation.choice.label,
            icon=operation.choice.icon,
            category=operation.choice.category,
            defaults=cast(JSON, operation.choice.defaults),
            config_schema=cast(JSON | None, operation.choice.config_schema),
            description=operation.description,
            selectable=operation.selectable,
            input_schema=cast(JSON | None, operation.input_schema),
            output_schema=cast(JSON | None, operation.output_schema),
            input_contract=WorkflowDataContract.from_contract(operation.input_contract),
            output_contract=WorkflowDataContract.from_contract(operation.output_contract),
            outcomes=[
                WorkflowStepOutcome(
                    key=outcome.key,
                    label=outcome.label,
                    description=outcome.description,
                )
                for outcome in operation.outcomes
            ],
            effect=operation.effect,
            effect_description=operation.effect_description,
            idempotent=operation.idempotent,
            subject_declaration=operation.subject_declaration,
        )


@strawberry.type
class WorkflowStepOperationQuery:
    """Admin-only workflow operation catalogue derived from the Step impl field."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_step_operations(self) -> list[WorkflowStepOperation]:
        """Return registered step operations in deterministic key order."""

        field = Step.impl_field("step_class")
        return [
            WorkflowStepOperation.from_operation(cast(type[StepImpl], field.resolve_class(key)).operation(key=key))
            for key in field.registered_keys()
        ]


@strawberry_django.type(Workflow)
class WorkflowType(AngeeNode):
    """Admin projection of a workflow definition."""

    key: auto
    name: auto
    description: auto
    purpose: auto
    subject_declaration: auto
    status: auto
    version: auto
    draft_revision: auto
    published_from: "WorkflowType | None"
    error_workflow: "WorkflowType | None"
    max_steps: auto
    budget: JSON
    created_at: auto
    updated_at: auto

    @strawberry_django.field(annotate=cast(Any, Workflow).lineage_projection_annotation())
    def lineage_id(self) -> PublicID:
        """Return the public id of this row's editable lineage head."""

        return cast(PublicID, to_public_id(Workflow, _workflow_projection(self)._workflow_lineage_id))

    @strawberry_django.field(annotate=cast(Any, Workflow).lineage_projection_annotation())
    def current_published_id(self) -> PublicID | None:
        """Return the current publication id, or null for an unpublished/retired lineage."""

        return to_public_id(Workflow, _workflow_projection(self)._workflow_current_published_pk)

    @strawberry_django.field(annotate=cast(Any, Workflow).lineage_projection_annotation())
    def current_published_version(self) -> int | None:
        """Return the current published version number."""

        return cast(int | None, _workflow_projection(self)._workflow_current_published_version)

    @strawberry_django.field(annotate=cast(Any, Workflow).lineage_projection_annotation())
    def current_published_subject_declaration(self) -> str | None:
        """Return current publication subject context, preserving a blank declaration."""

        return cast(str | None, _workflow_projection(self)._workflow_current_published_subject_declaration)

    @strawberry_django.field(annotate=cast(Any, Workflow).lineage_projection_annotation())
    def publication_status(self) -> str:
        """Return the lineage publication state independently of the editable head."""

        return cast(str, _workflow_projection(self)._workflow_publication_status)


def _workflow_projection(value: Any) -> Any:
    """Ensure mutation-returned rows use the same lineage projection as queries."""

    if hasattr(value, "_workflow_lineage_id"):
        return value
    projected = Workflow.objects.with_lineage_projection().get(pk=value.pk)
    for name in Workflow.lineage_projection_annotation():
        setattr(value, name, getattr(projected, name))
    return value


@strawberry_django.type(Step)
class StepType(AngeeNode):
    """Admin projection of a workflow step definition."""

    workflow: WorkflowType
    key: auto
    name: auto
    step_class: auto

    @strawberry_django.field
    def config(self) -> JSON:
        """Return canonical config without rewriting historical workflow rows."""

        return cast(Any, self).config_projection().value

    @strawberry_django.field
    def config_errors(self) -> JSON:
        """Return typed-config diagnostics while keeping malformed raw values repairable."""

        return cast(Any, self).config_projection().errors

    input_binding: JSON | None

    join_rule: auto
    is_entry: auto
    position: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Edge)
class EdgeType(AngeeNode):
    """Admin projection of a workflow edge definition."""

    workflow: WorkflowType
    source: StepType
    target: StepType
    condition: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Trigger)
class TriggerType(AngeeNode):
    """Admin projection of a workflow trigger definition."""

    workflow: WorkflowType
    kind: auto
    enabled: auto
    config: JSON
    next_fire_at: auto
    last_fire_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["kind", "config"])
    def summary(self) -> str | None:
        """Return the declaration-owned readable rule when the row is valid."""

        try:
            return cast(Any, self).validated_config().summary()
        except ValidationError:
            return None

    @strawberry_django.field(only=["kind", "config", "workflow"])
    def activation_blocker(self) -> str | None:
        """Explain why this rule cannot currently be enabled."""

        return cast(Any, self).activation_blocker(
            has_current_publication=cast(bool | None, getattr(self, "_trigger_has_current_publication", None))
        )


@strawberry.type
class WorkflowTriggerDeclaration:
    kind: str
    label: str
    config_schema: JSON


@strawberry.type
class WorkflowTriggerPublisher:
    model: str
    label: str


@strawberry.type
class WorkflowSchedulePreview:
    timezone: str
    occurrences: list[datetime]
    errors: list[str]


@strawberry.type
class WorkflowTriggerDeclarationQuery:
    """Admin-only trigger authoring facts from their native owners."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_trigger_declarations(self) -> list[WorkflowTriggerDeclaration]:
        return [
            WorkflowTriggerDeclaration(
                kind=value,
                label=str(TriggerKind(value).label),
                config_schema=cast(JSON, trigger_config_schema(value)),
            )
            for value in trigger_kind_names()
        ]

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_trigger_publishers(self) -> list[WorkflowTriggerPublisher]:
        from angee.graphql.schema import GraphQLSchemas

        return [
            WorkflowTriggerPublisher(model=model._meta.label_lower, label=str(model._meta.verbose_name))
            for model in GraphQLSchemas.from_discovery().change_publisher_models()
        ]

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_schedule_preview(self, config: JSON, count: int = 3) -> WorkflowSchedulePreview:
        result = schedule_draft_preview(config, now=timezone.now(), count=count)
        return WorkflowSchedulePreview(
            timezone="UTC",
            occurrences=list(result.occurrences),
            errors=list(result.errors),
        )


def _trigger_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
    """Load trigger rule projections and publication availability in one bounded query."""

    del info
    current = Workflow.objects.current_published().filter(published_from_id=models.OuterRef("workflow_id"))
    return Trigger.objects.all().select_related("workflow").annotate(
        _trigger_has_current_publication=models.Exists(current)
    )


@strawberry_django.type(WorkflowRun)
class WorkflowRunType(AngeeNode):
    """Admin projection of a workflow run."""

    workflow: WorkflowType
    origin: auto
    trigger: TriggerType | None
    parent_step_run: "StepRunType | None"
    status: auto
    subject_object_id: auto
    wake_at: auto
    steps_taken: auto
    budget_spent: JSON
    error: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(annotate=cast(Any, WorkflowRun).waiting_projection_annotation())
    def waiting_kind(self) -> str | None:
        """Return the declared runtime wait reason, when one is known."""

        return cast(str, cast(Any, self)._workflow_waiting_kind) or None

    @strawberry_django.field(annotate=cast(Any, WorkflowRun).waiting_projection_annotation())
    def next_wake_at(self) -> datetime | None:
        """Return the next genuine scheduled wake, excluding external waits."""

        return cast(Any, self)._workflow_next_wake_at


@strawberry_django.type(StepRun)
class StepRunType(AngeeNode):
    """Admin projection of one workflow step-run journal row."""

    run: WorkflowRunType
    step: StepType | None
    system_kind: auto
    map_index: auto
    status: auto
    input: JSON
    output: JSON
    resume_state: JSON
    outcome: auto
    attempt: auto
    wait_until: auto
    heartbeat_at: auto
    error: auto
    stacktrace: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["waiting_kind"])
    def waiting_kind(self) -> str | None:
        """Return a declared wait reason, or null for legacy/nonwaiting rows."""

        return str(cast(Any, self).waiting_kind) or None


@strawberry_django.type(Decision)
class DecisionType(AngeeNode):
    """Admin projection of one awaited workflow decision."""

    step_run: StepRunType
    priority: auto
    action: auto
    payload: JSON
    verdict: auto
    resolution: JSON
    resolved_by: auto
    attempts: auto
    max_attempts: auto
    expires_at: auto
    escalate_at: auto
    created_at: auto
    updated_at: auto

    decision_schema: JSON | None = _decision_schema_field()


@strawberry_django.type(Decision, name="DecisionType")
class PublicDecisionType(AngeeNode):
    """Public projection of one awaited workflow decision."""

    priority: auto
    action: auto
    payload: JSON
    verdict: auto
    resolution: JSON
    resolved_by: auto
    attempts: auto
    max_attempts: auto
    expires_at: auto
    escalate_at: auto
    created_at: auto
    updated_at: auto

    decision_schema: JSON | None = _decision_schema_field()

    @strawberry_django.field(
        only=["id"],
        annotate={
            _PROJECTED_STEP_ID: models.F("step_run__step_id"),
            _PROJECTED_STEP_WORKFLOW_NAME: models.F("step_run__step__workflow__name"),
            _PROJECTED_RUN_WORKFLOW_NAME: models.F("step_run__run__workflow__name"),
        },
    )
    def workflow_name(self) -> str:
        """Return the workflow display name without exposing the StepRun journal."""

        if getattr(self, _PROJECTED_STEP_ID) is not None:
            return str(getattr(self, _PROJECTED_STEP_WORKFLOW_NAME))
        return str(getattr(self, _PROJECTED_RUN_WORKFLOW_NAME))

    @strawberry_django.field(
        only=["id"],
        annotate={
            _PROJECTED_STEP_ID: models.F("step_run__step_id"),
            _PROJECTED_STEP_NAME: models.F("step_run__step__name"),
            _PROJECTED_STEP_KEY: models.F("step_run__step__key"),
            _PROJECTED_SYSTEM_KIND: models.F("step_run__system_kind"),
            _PROJECTED_STEP_RUN_ID: models.F("step_run_id"),
        },
    )
    def step_name(self) -> str:
        """Return the step display name without exposing the StepRun journal."""

        if getattr(self, _PROJECTED_STEP_ID) is not None:
            return str(getattr(self, _PROJECTED_STEP_NAME) or getattr(self, _PROJECTED_STEP_KEY))
        return str(getattr(self, _PROJECTED_SYSTEM_KIND) or getattr(self, _PROJECTED_STEP_RUN_ID))


@strawberry.type(name="DecisionResolutionPayload")
class PublicDecisionResolutionPayload:
    """Public decision state plus in-band validation errors from one attempt."""

    decision: PublicDecisionType
    validation_errors: JSON | None = None

    @classmethod
    def from_result(cls, result: engine.DecisionAttemptResult) -> PublicDecisionResolutionPayload:
        """Return the public payload for an engine-owned decision attempt."""

        return cls(
            decision=cast(PublicDecisionType, result.decision),
            validation_errors=ActionResult.validation_error_map(
                result.validation_error,
                camel_case_keys=False,
            ),
        )


@strawberry.type(name="DecisionResolutionPayload")
class ConsoleDecisionResolutionPayload:
    """Console decision state plus in-band validation errors from one attempt."""

    decision: DecisionType
    validation_errors: JSON | None = None

    @classmethod
    def from_result(cls, result: engine.DecisionAttemptResult) -> ConsoleDecisionResolutionPayload:
        """Return the console payload for an engine-owned decision attempt."""

        return cls(
            decision=cast(DecisionType, result.decision),
            validation_errors=ActionResult.validation_error_map(
                result.validation_error,
                camel_case_keys=False,
            ),
        )


@strawberry.input
class WorkflowObjectRefInput:
    """Generic subject reference for starting a workflow run."""

    subject_declaration: str
    id: PublicID


@strawberry.input
class WorkflowDefinitionPatchInput:
    name: str | None = strawberry.UNSET
    description: str | None = strawberry.UNSET
    purpose: str | None = strawberry.UNSET
    subject_declaration: str | None = strawberry.UNSET
    error_workflow: PublicID | None = strawberry.UNSET
    max_steps: int | None = strawberry.UNSET
    budget: JSON | None = strawberry.UNSET


@strawberry.input
class WorkflowNodeFieldsInput:
    key: str | None = strawberry.UNSET
    name: str | None = strawberry.UNSET
    step_class: str | None = strawberry.UNSET
    config: JSON | None = strawberry.UNSET
    input_binding: JSON | None = strawberry.UNSET
    join_rule: str | None = strawberry.UNSET
    is_entry: bool | None = strawberry.UNSET
    position: JSON | None = strawberry.UNSET


@strawberry.input
class WorkflowNodeCreateInput:
    client_key: str
    fields: WorkflowNodeFieldsInput


@strawberry.input
class WorkflowNodePatchInput:
    id: PublicID
    fields: WorkflowNodeFieldsInput


@strawberry.input
class WorkflowEndpointInput:
    id: PublicID | None = strawberry.UNSET
    client_key: str | None = strawberry.UNSET


@strawberry.input
class WorkflowEdgeFieldsInput:
    condition: str | None = strawberry.UNSET


@strawberry.input
class WorkflowEdgeCreateInput:
    client_key: str
    source: WorkflowEndpointInput
    target: WorkflowEndpointInput
    fields: WorkflowEdgeFieldsInput | None = None


@strawberry.input
class WorkflowEdgePatchInput:
    id: PublicID
    fields: WorkflowEdgeFieldsInput | None = None
    source: WorkflowEndpointInput | None = strawberry.UNSET
    target: WorkflowEndpointInput | None = strawberry.UNSET


@strawberry.input
class WorkflowDefinitionEditInput:
    workflow: WorkflowDefinitionPatchInput | None = None
    node_creates: list[WorkflowNodeCreateInput] | None = None
    node_patches: list[WorkflowNodePatchInput] | None = None
    node_deletes: list[PublicID] | None = None
    edge_creates: list[WorkflowEdgeCreateInput] | None = None
    edge_patches: list[WorkflowEdgePatchInput] | None = None
    edge_deletes: list[PublicID] | None = None


@strawberry.enum
class WorkflowDefinitionStatus(Enum):
    SUCCESS = "success"
    STALE = "stale"
    STRUCTURAL = "structural"
    READINESS = "readiness"


@strawberry.type
class WorkflowDefinitionDiagnostic:
    code: str
    message: str
    kind: str
    id: PublicID | None
    client_key: str | None
    requested_id: str | None
    field: str
    detail_path: JSON


@strawberry.type
class WorkflowDefinitionCorrelation:
    client_key: str
    id: PublicID


@strawberry.type
class WorkflowDefinitionPayload:
    status: WorkflowDefinitionStatus
    revision: int | None = None
    current_revision: int | None = None
    publication: WorkflowType | None = None
    publication_created: bool | None = None
    nodes: list[WorkflowDefinitionCorrelation] = strawberry.field(default_factory=list)
    edges: list[WorkflowDefinitionCorrelation] = strawberry.field(default_factory=list)
    diagnostics: list[WorkflowDefinitionDiagnostic] = strawberry.field(default_factory=list)


@strawberry.type
class WorkflowDefinitionNode:
    id: PublicID
    key: str
    name: str
    step_class: str
    config: JSON
    config_errors: JSON
    input_binding: JSON | None
    join_rule: str
    is_entry: bool
    position: JSON


@strawberry.type
class WorkflowDefinitionEdge:
    id: PublicID
    source: PublicID
    target: PublicID
    condition: str


@strawberry.type
class WorkflowDefinitionSnapshot:
    workflow: WorkflowType
    revision: int
    nodes: list[WorkflowDefinitionNode]
    edges: list[WorkflowDefinitionEdge]
    readiness: list[WorkflowDefinitionDiagnostic]


@strawberry.type
class WorkflowInputSource:
    kind: str
    id: PublicID | None
    client_key: str | None
    step_key: str | None
    label: str | None
    contract: WorkflowDataContract


@strawberry.type
class WorkflowInputSourcesPayload:
    status: WorkflowDefinitionStatus
    revision: int | None = None
    current_revision: int | None = None
    sources: list[WorkflowInputSource] = strawberry.field(default_factory=list)
    diagnostics: list[WorkflowDefinitionDiagnostic] = strawberry.field(default_factory=list)


_WORKFLOW_RESOURCE = hasura_model_resource(
    WorkflowType,
    model=Workflow,
    name="workflows",
    filterable=[
        "id",
        "key",
        "name",
        "subject_declaration",
        "purpose",
        "status",
        "version",
        "published_from",
        "error_workflow",
        "updated_at",
    ],
    sortable=["key", "name", "purpose", "status", "version", "created_at", "updated_at"],
    aggregatable=["id", "version", "max_steps"],
    groupable=["purpose", "status", "updated_at"],
    insertable=[
        "key",
        "name",
        "description",
        "purpose",
        "subject_declaration",
        "error_workflow",
        "max_steps",
        "budget",
    ],
    updatable=[
        "key",
        "name",
        "description",
        "purpose",
        "subject_declaration",
        "error_workflow",
        "max_steps",
        "budget",
    ],
    field_id_decode={
        "published_from": public_pk_decoder(Workflow),
        "error_workflow": public_pk_decoder(Workflow),
    },
    write_backend=AngeeHasuraWriteBackend(Workflow, public_id_fields=("error_workflow",)),
)
_STEP_RESOURCE = hasura_model_resource(
    StepType,
    model=Step,
    name="workflow_steps",
    filterable=["id", "workflow", "key", "name", "step_class", "join_rule", "is_entry", "updated_at"],
    sortable=["workflow", "key", "name", "step_class", "join_rule", "is_entry", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["workflow", "workflow__name", "step_class", "join_rule", "is_entry", "updated_at"],
    insertable=[
        "workflow",
        "key",
        "name",
        "step_class",
        "config",
        "input_binding",
        "join_rule",
        "is_entry",
        "position",
    ],
    updatable=["key", "name", "step_class", "config", "input_binding", "join_rule", "is_entry", "position"],
    field_id_decode={"workflow": public_pk_decoder(Workflow)},
    write_backend=AngeeHasuraWriteBackend(Step, public_id_fields=("workflow",)),
)
_EDGE_RESOURCE = hasura_model_resource(
    EdgeType,
    model=Edge,
    name="workflow_edges",
    filterable=["id", "workflow", "source", "target", "condition", "updated_at"],
    sortable=["workflow", "source", "target", "condition", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["workflow", "workflow__name", "condition", "updated_at"],
    insertable=["workflow", "source", "target", "condition"],
    updatable=["source", "target", "condition"],
    field_id_decode={
        "workflow": public_pk_decoder(Workflow),
        "source": public_pk_decoder(Step),
        "target": public_pk_decoder(Step),
    },
    write_backend=AngeeHasuraWriteBackend(Edge, public_id_fields=("workflow", "source", "target")),
)
_TRIGGER_RESOURCE = hasura_model_resource(
    TriggerType,
    model=Trigger,
    name="workflow_triggers",
    filterable=["id", "workflow", "kind", "enabled", "next_fire_at", "updated_at"],
    sortable=["workflow", "kind", "enabled", "next_fire_at", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["workflow", "workflow__name", "kind", "enabled", "updated_at"],
    insertable=["workflow", "kind", "config"],
    updatable=["kind", "config"],
    field_id_decode={"workflow": public_pk_decoder(Workflow)},
    get_queryset=_trigger_queryset,
    write_backend=AngeeHasuraWriteBackend(Trigger, public_id_fields=("workflow",)),
)
_WORKFLOW_RUN_RESOURCE = hasura_model_resource(
    WorkflowRunType,
    model=WorkflowRun,
    name="workflow_runs",
    filterable=[
        "id",
        "workflow",
        "workflow__purpose",
        "workflow__published_from",
        "trigger",
        "parent_step_run",
        "status",
        "origin",
        "wake_at",
        "updated_at",
    ],
    sortable=["workflow", "status", "wake_at", "steps_taken", "created_at", "updated_at"],
    aggregatable=["id", "steps_taken"],
    groupable=["workflow", "workflow__name", "origin", "status", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "workflow": public_pk_decoder(Workflow),
        "workflow__published_from": public_pk_decoder(Workflow),
        "trigger": public_pk_decoder(Trigger),
        "parent_step_run": public_pk_decoder(StepRun),
    },
)
_STEP_RUN_RESOURCE = hasura_model_resource(
    StepRunType,
    model=StepRun,
    name="workflow_step_runs",
    filterable=[
        "id",
        "run",
        "step",
        "system_kind",
        "map_index",
        "status",
        "outcome",
        "wait_until",
        "waiting_kind",
        "updated_at",
    ],
    sortable=["run", "step", "map_index", "status", "attempt", "created_at", "updated_at"],
    aggregatable=["id", "attempt"],
    groupable=["run", "step", "step__key", "system_kind", "status", "waiting_kind", "outcome", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "run": public_pk_decoder(WorkflowRun),
        "step": public_pk_decoder(Step),
    },
)
_DECISION_RESOURCE = hasura_model_resource(
    DecisionType,
    model=Decision,
    name="workflow_decisions",
    filterable=[
        "id",
        "step_run",
        "priority",
        "action",
        "verdict",
        "expires_at",
        "escalate_at",
        "updated_at",
    ],
    sortable=[
        "step_run",
        "priority",
        "action",
        "verdict",
        "expires_at",
        "escalate_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "priority", "attempts"],
    groupable=["step_run", "action", "verdict", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={"step_run": public_pk_decoder(StepRun)},
)
_PUBLIC_DECISION_RESOURCE = hasura_model_resource(
    PublicDecisionType,
    model=Decision,
    name="workflow_decisions",
    filterable=[
        "id",
        "priority",
        "action",
        "verdict",
        "expires_at",
        "escalate_at",
        "updated_at",
    ],
    sortable=[
        "priority",
        "action",
        "verdict",
        "expires_at",
        "escalate_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "priority", "attempts"],
    groupable=["action", "verdict", "updated_at"],
    insert=False,
    update=False,
    delete=False,
)


@strawberry.type
class WorkflowSubjectDeclarationQuery:
    """Actor-scoped workflow discovery for one record resource."""

    @strawberry.field
    def workflows_for_subject_declaration(
        self,
        info: strawberry.Info,
        subject_declaration: str,
    ) -> list[WorkflowType]:
        """Return current workflows the actor may start for this subject declaration."""

        actor = session_user(info)
        scoped = read_scoped_queryset(cast(type[models.Model], Workflow), actor, action="write")
        if scoped is None:
            return []
        workflows = cast(Any, scoped).for_subject_declaration(subject_declaration)
        return cast(list[WorkflowType], workflows)


@strawberry.type
class WorkflowActionMutation:
    """Console actions for workflow definition lifecycle."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def publish_workflow(self, workflow: PublicID) -> ActionResult:
        """Publish a draft workflow lineage head."""

        target = resolve_action_target(Workflow, workflow, reason="workflows.graphql.publish_workflow")
        try:
            published = target.publish()
        except ValidationError as error:
            return ActionResult(ok=False, message=f"Publish failed: {error}")
        return ActionResult(ok=True, message=f"Published workflow {published.sqid}.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def save_workflow_definition(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        edit: WorkflowDefinitionEditInput,
    ) -> WorkflowDefinitionPayload:
        """Atomically save one draft graph against its acknowledged revision."""

        target = authorized_action_target(info, Workflow, workflow, "write")
        try:
            command = _definition_edit(target, edit)
            result = Workflow.objects.apply_definition(
                target,
                expected_revision=expected_revision,
                edit=command,
            )
        except StaleDefinitionError as error:
            return WorkflowDefinitionPayload(
                status=WorkflowDefinitionStatus.STALE,
                current_revision=error.current,
            )
        except DefinitionEditError as error:
            return WorkflowDefinitionPayload(
                status=WorkflowDefinitionStatus.STRUCTURAL,
                revision=expected_revision,
                diagnostics=_definition_diagnostics(error.diagnostics),
            )
        return WorkflowDefinitionPayload(
            status=WorkflowDefinitionStatus.SUCCESS,
            revision=result.revision,
            nodes=[
                WorkflowDefinitionCorrelation(
                    client_key=item.client_key,
                    id=cast(PublicID, to_public_id(Step, item.identity)),
                )
                for item in result.nodes
            ],
            edges=[
                WorkflowDefinitionCorrelation(
                    client_key=item.client_key,
                    id=cast(PublicID, to_public_id(Edge, item.identity)),
                )
                for item in result.edges
            ],
            diagnostics=_definition_diagnostics(result.readiness),
        )

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def publish_workflow_definition(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
    ) -> WorkflowDefinitionPayload:
        """Publish exactly one saved draft revision."""

        target = authorized_action_target(info, Workflow, workflow, "write")
        try:
            result = Workflow.objects.publish_definition(target, expected_revision=expected_revision)
        except StaleDefinitionError as error:
            return WorkflowDefinitionPayload(
                status=WorkflowDefinitionStatus.STALE,
                current_revision=error.current,
            )
        except DefinitionReadinessError as error:
            return WorkflowDefinitionPayload(
                status=WorkflowDefinitionStatus.READINESS,
                revision=expected_revision,
                diagnostics=_definition_diagnostics(error.diagnostics),
            )
        except DefinitionEditError as error:
            return WorkflowDefinitionPayload(
                status=WorkflowDefinitionStatus.STRUCTURAL,
                revision=expected_revision,
                diagnostics=_definition_diagnostics(error.diagnostics),
            )
        return WorkflowDefinitionPayload(
            status=WorkflowDefinitionStatus.SUCCESS,
            revision=result.revision,
            publication=cast(WorkflowType, result.publication),
            publication_created=result.created,
        )


@strawberry.type
class WorkflowDefinitionQuery:
    """Coherent workflow definition reads for the console editor."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_definition(self, info: strawberry.Info, workflow: PublicID) -> WorkflowDefinitionSnapshot:
        target = authorized_action_target(info, Workflow, workflow, "read")
        snapshot = Workflow.objects.definition_snapshot(target)
        return WorkflowDefinitionSnapshot(
            workflow=cast(WorkflowType, snapshot.workflow),
            revision=snapshot.revision,
            nodes=[
                WorkflowDefinitionNode(
                    id=row.sqid,
                    key=row.key,
                    name=row.name,
                    step_class=row.step_class,
                    config=cast(JSON, row.config_projection().value),
                    config_errors=cast(JSON, row.config_projection().errors),
                    input_binding=cast(JSON | None, row.input_binding),
                    join_rule=str(row.join_rule),
                    is_entry=row.is_entry,
                    position=cast(JSON, row.position),
                )
                for row in snapshot.nodes
            ],
            edges=[
                WorkflowDefinitionEdge(
                    id=row.sqid,
                    source=row.source.sqid,
                    target=row.target.sqid,
                    condition=row.condition,
                )
                for row in snapshot.edges
            ],
            readiness=_definition_diagnostics(snapshot.readiness),
        )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_input_sources(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        edit: WorkflowDefinitionEditInput,
        target: WorkflowEndpointInput,
    ) -> WorkflowInputSourcesPayload:
        """Project sources from the caller's exact unsaved definition without persisting it."""

        owner = authorized_action_target(info, Workflow, workflow, "write")
        try:
            result = Workflow.objects.definition_input_sources(
                owner,
                expected_revision=expected_revision,
                edit=_definition_edit(owner, edit),
                target=_endpoint_ref(owner, target),
            )
        except StaleDefinitionError as error:
            return WorkflowInputSourcesPayload(
                status=WorkflowDefinitionStatus.STALE,
                current_revision=error.current,
            )
        except DefinitionEditError as error:
            return WorkflowInputSourcesPayload(
                status=WorkflowDefinitionStatus.STRUCTURAL,
                revision=expected_revision,
                diagnostics=_definition_diagnostics(error.diagnostics),
            )
        return WorkflowInputSourcesPayload(
            status=WorkflowDefinitionStatus.SUCCESS,
            revision=result.revision,
            sources=[
                WorkflowInputSource(
                    kind=source.kind,
                    id=(
                        cast(PublicID, to_public_id(Step, source.node_identity.existing_id))
                        if source.node_identity is not None and source.node_identity.existing_id is not None
                        else None
                    ),
                    client_key=source.node_identity.client_key if source.node_identity is not None else None,
                    step_key=source.step_key,
                    label=source.label,
                    contract=WorkflowDataContract.from_contract(source.contract),
                )
                for source in result.sources
            ],
            diagnostics=_definition_diagnostics(result.readiness),
        )


def _definition_edit(workflow: Any, value: WorkflowDefinitionEditInput) -> DefinitionEdit:
    """Translate typed transport values to the domain command without deciding policy."""

    workflow_fields = _set_fields(value.workflow)
    if "error_workflow" in workflow_fields and workflow_fields["error_workflow"] is not None:
        related = instance_for_id(
            Workflow,
            workflow_fields["error_workflow"],
            queryset=Workflow.objects.with_action("read"),
        )
        if related is None:
            raise DefinitionEditError(
                (
                    GraphDiagnostic(
                        "reference_invalid",
                        "Error workflow is missing or unavailable.",
                        GraphLocation("workflow", GraphIdentity(existing_id=workflow.pk), "error_workflow"),
                    ),
                )
            )
        workflow_fields["error_workflow"] = related
    return DefinitionEdit(
        workflow=workflow_fields,
        node_creates=tuple(NodeCreate(item.client_key, _set_fields(item.fields)) for item in value.node_creates or ()),
        node_patches=tuple(_node_patch(item) for item in value.node_patches or ()),
        node_deletes=tuple(_node_delete(item) for item in value.node_deletes or ()),
        edge_creates=tuple(
            EdgeCreate(
                item.client_key,
                _endpoint_ref(workflow, item.source),
                _endpoint_ref(workflow, item.target),
                _set_fields(item.fields),
            )
            for item in value.edge_creates or ()
        ),
        edge_patches=tuple(_edge_patch(workflow, item) for item in value.edge_patches or ()),
        edge_deletes=tuple(_edge_delete(item) for item in value.edge_deletes or ()),
    )


def _set_fields(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    return {name: field_value for name, field_value in vars(value).items() if field_value is not strawberry.UNSET}


def _definition_pk(model: type[models.Model], value: PublicID) -> int:
    """Decode a public id without resolving or authorizing the referenced row."""

    try:
        public_field = public_data_id_field(model)
        if public_field is None:
            return -1
        resolved = public_field.public_id_to_value(value)
    except TypeError, ValueError, ValidationError:
        return -1
    return resolved if type(resolved) is int else -1


def _definition_reference(model: type[models.Model], value: PublicID) -> tuple[int, str]:
    return _definition_pk(model, value), str(value)


def _node_patch(item: WorkflowNodePatchInput) -> NodePatch:
    identity, requested_id = _definition_reference(Step, item.id)
    return NodePatch(identity, _set_fields(item.fields), requested_id)


def _node_delete(value: PublicID) -> NodeDelete:
    identity, requested_id = _definition_reference(Step, value)
    return NodeDelete(identity, requested_id)


def _edge_patch(workflow: Any, item: WorkflowEdgePatchInput) -> EdgePatch:
    identity, requested_id = _definition_reference(Edge, item.id)
    return EdgePatch(
        identity,
        _set_fields(item.fields),
        None if item.source is strawberry.UNSET else _endpoint_ref(workflow, cast(Any, item.source)),
        None if item.target is strawberry.UNSET else _endpoint_ref(workflow, cast(Any, item.target)),
        requested_id,
    )


def _edge_delete(value: PublicID) -> EdgeDelete:
    identity, requested_id = _definition_reference(Edge, value)
    return EdgeDelete(identity, requested_id)


def _endpoint_ref(workflow: Any, value: WorkflowEndpointInput | None) -> EndpointRef:
    if value is None:
        return EndpointRef()
    existing = None
    if value.id is not strawberry.UNSET and value.id is not None:
        existing = _definition_pk(Step, value.id)
    client_key = None if value.client_key is strawberry.UNSET else value.client_key
    return EndpointRef(existing_id=existing, client_key=client_key)


def _definition_diagnostics(values: tuple[GraphDiagnostic, ...]) -> list[WorkflowDefinitionDiagnostic]:
    return [
        WorkflowDefinitionDiagnostic(
            code=value.code,
            message=value.message,
            kind=value.location.kind,
            id=_definition_public_identity(value),
            client_key=_definition_client_identity(value),
            requested_id=value.location.key.requested_id,
            field=value.location.field,
            detail_path=cast(JSON, list(value.location.detail_path)),
        )
        for value in values
    ]


def _definition_public_identity(value: GraphDiagnostic) -> PublicID | None:
    identity = value.location.key
    if identity.existing_id is None:
        return None
    pk = identity.existing_id
    model = {"workflow": Workflow, "node": Step, "edge": Edge}[value.location.kind]
    return to_public_id(model, pk)


def _definition_client_identity(value: GraphDiagnostic) -> str | None:
    return value.location.key.client_key


@strawberry.type
class PublicDecisionMutation:
    """Public decision resolution mutation."""

    @strawberry.mutation
    def decide(
        self,
        info: strawberry.Info,
        decision: PublicID,
        verdict: DecisionVerb,
        payload: JSON | None = None,
    ) -> PublicDecisionResolutionPayload:
        """Resolve one pending decision as the signed-in session actor."""

        actor = session_user(info)
        target = resolve_action_target(Decision, decision, reason="workflows.graphql.decide")
        result = engine.decide(target, verdict.value, payload=payload, actor=actor)
        return PublicDecisionResolutionPayload.from_result(result)


@strawberry.type
class ConsoleDecisionMutation:
    """Console decision resolution mutation."""

    @strawberry.mutation
    def decide(
        self,
        info: strawberry.Info,
        decision: PublicID,
        verdict: DecisionVerb,
        payload: JSON | None = None,
    ) -> ConsoleDecisionResolutionPayload:
        """Resolve one pending decision as the signed-in session actor."""

        actor = session_user(info)
        target = resolve_action_target(Decision, decision, reason="workflows.graphql.decide")
        result = engine.decide(target, verdict.value, payload=payload, actor=actor)
        return ConsoleDecisionResolutionPayload.from_result(result)


@strawberry.type
class WorkflowRunActionMutation:
    """Console actions for workflow run lifecycle."""

    @strawberry.mutation
    @action_guard("Run workflow failed.")
    def start_workflow_run(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        subject: WorkflowObjectRefInput | None = None,
    ) -> ActionResult:
        """Start the current published version of a workflow lineage."""

        actor = session_user(info)
        target = authorized_action_target(info, Workflow, workflow, "write")
        run = engine.start(
            target,
            subject=_resolve_subject(subject, actor=actor),
            actor=actor,
        )
        return ActionResult(ok=True, message=f"Started workflow run {run.sqid}.", id=run.sqid)

    @strawberry.mutation
    @action_guard("Test workflow failed.")
    def start_workflow_test(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        request_key: str,
        subject: WorkflowObjectRefInput | None = None,
        input: JSON | None = strawberry.UNSET,
    ) -> ActionResult:
        """Start or recover a whole-workflow test of one saved revision."""

        actor = session_user(info)
        target = authorized_action_target(info, Workflow, workflow, "write")
        run = WorkflowRun.objects.start_test(
            target,
            expected_revision=expected_revision,
            request_key=request_key,
            subject=_resolve_subject(subject, actor=actor),
            actor=actor,
            input=JsonPresence(input is not strawberry.UNSET, None if input is strawberry.UNSET else input),
        )
        return ActionResult(ok=True, message=f"Started workflow test {run.sqid}.", id=run.sqid)

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def cancel_workflow_run(self, run: PublicID) -> ActionResult:
        """Cancel a workflow run and its active journal rows."""

        with action_target(WorkflowRun, run, reason="workflows.graphql.cancel_workflow_run") as target:
            engine.cancel(target)
        return ActionResult(ok=True, message="Workflow run canceled.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def override_run(self, info: strawberry.Info, run: PublicID, next_steps: list[PublicID]) -> ActionResult:
        """Cancel active rows and schedule chosen next steps through an override row."""

        actor = session_user(info)
        target = resolve_action_target(WorkflowRun, run, reason="workflows.graphql.override_run")
        steps = [
            resolve_action_target(Step, step_id, reason="workflows.graphql.override_run.step") for step_id in next_steps
        ]
        override = engine.override_run(target, steps, actor=actor)
        return ActionResult(ok=True, message=f"Override recorded as {override.sqid}.")


@strawberry.type
class TriggerActionMutation:
    """Console actions for workflow trigger lifecycle."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def enable_workflow_trigger(self, trigger: PublicID) -> ActionResult:
        """Enable a workflow trigger."""

        with action_target(Trigger, trigger, reason="workflows.graphql.enable_workflow_trigger") as target:
            target.enable()
        return ActionResult(ok=True, message="Workflow trigger enabled.")

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def disable_workflow_trigger(self, trigger: PublicID) -> ActionResult:
        """Disable a workflow trigger."""

        with action_target(Trigger, trigger, reason="workflows.graphql.disable_workflow_trigger") as target:
            target.disable()
        return ActionResult(ok=True, message="Workflow trigger disabled.")


_CONSOLE_TYPES: list[object] = [
    DecisionVerb,
    WorkflowStepEffectEnum,
    WorkflowStepOutcome,
    WorkflowStepOperation,
    WorkflowType,
    StepType,
    EdgeType,
    TriggerType,
    WorkflowRunType,
    StepRunType,
    DecisionType,
    WorkflowObjectRefInput,
    WorkflowDefinitionPatchInput,
    WorkflowNodeFieldsInput,
    WorkflowNodeCreateInput,
    WorkflowNodePatchInput,
    WorkflowEndpointInput,
    WorkflowEdgeFieldsInput,
    WorkflowEdgeCreateInput,
    WorkflowEdgePatchInput,
    WorkflowDefinitionEditInput,
    WorkflowDefinitionStatus,
    WorkflowDefinitionDiagnostic,
    WorkflowDefinitionCorrelation,
    WorkflowDefinitionPayload,
    WorkflowDefinitionNode,
    WorkflowDefinitionEdge,
    WorkflowDefinitionSnapshot,
    *_WORKFLOW_RESOURCE.types,
    *_STEP_RESOURCE.types,
    *_EDGE_RESOURCE.types,
    *_TRIGGER_RESOURCE.types,
    *_WORKFLOW_RUN_RESOURCE.types,
    *_STEP_RUN_RESOURCE.types,
    *_DECISION_RESOURCE.types,
]

_PUBLIC_TYPES: list[object] = [
    DecisionVerb,
    PublicDecisionType,
    *_PUBLIC_DECISION_RESOURCE.types,
]

schemas = {
    "public": {
        "query": [_PUBLIC_DECISION_RESOURCE.query],
        "mutation": [PublicDecisionMutation],
        "subscription": [changes(Decision, field="decisionChanged")],
        "types": _PUBLIC_TYPES,
    },
    "console": {
        "query": [
            WorkflowSubjectDeclarationQuery,
            WorkflowStepOperationQuery,
            WorkflowTriggerDeclarationQuery,
            WorkflowDefinitionQuery,
            _WORKFLOW_RESOURCE.query,
            _STEP_RESOURCE.query,
            _EDGE_RESOURCE.query,
            _TRIGGER_RESOURCE.query,
            _WORKFLOW_RUN_RESOURCE.query,
            _STEP_RUN_RESOURCE.query,
            _DECISION_RESOURCE.query,
        ],
        "mutation": [
            _WORKFLOW_RESOURCE.mutation,
            _STEP_RESOURCE.mutation,
            _EDGE_RESOURCE.mutation,
            _TRIGGER_RESOURCE.mutation,
            WorkflowActionMutation,
            WorkflowRunActionMutation,
            TriggerActionMutation,
            ConsoleDecisionMutation,
        ],
        "subscription": [
            changes(WorkflowRun, field="workflowRunChanged"),
            changes(Decision, field="decisionChanged"),
        ],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the workflows addon."""


def _resolve_subject(ref: WorkflowObjectRefInput | None, *, actor: Any) -> models.Model | None:
    """Resolve an optional run subject through the actor's write scope.

    Starting a workflow operates on the record — its steps may mutate the
    subject elevated — so the subject requires ``write``, matching the
    decision-resolution relation re-check (`engine._relation_error`).
    """

    if ref is None:
        return None
    try:
        model = cast(type[models.Model], apps.get_model(ref.subject_declaration))
    except (LookupError, ValueError) as error:
        raise ValidationError({"subject": "Run workflow subject declaration is not installed."}) from error
    queryset = read_scoped_queryset(model, actor, action="write")
    if queryset is None:
        queryset = model._default_manager.all()
    subject = instance_for_id(model, ref.id, queryset=queryset)
    if subject is None:
        raise ValidationError({"subject": "Run workflow subject was not found."})
    return cast(models.Model, subject)
