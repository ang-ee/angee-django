"""GraphQL schema contributions for the workflows addon."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import actor_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import public_data_id_field
from angee.base.refs import canonical_record_target
from angee.base.scoping import read_scoped_queryset
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    action_target,
    authorized_action_target,
    resolve_action_target,
)
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.data import declared_hasura_resource_fields
from angee.graphql.data.metadata import readable_model_field_names
from angee.graphql.ids import PublicID, instance_for_id, to_public_id
from angee.graphql.impl import ImplChoice as GraphQLImplChoice
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
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
from angee.workflows.testing import FixtureRole, FixtureSpec, WorkflowScope
from angee.workflows.trigger_conditions import EventConditionCatalogue, EventConditionClause
from angee.workflows.trigger_declarations import (
    EventTriggerConfig,
    schedule_draft_preview,
    trigger_config_schema,
    trigger_kind_names,
)

User = get_user_model()

Workflow = apps.get_model("workflows", "Workflow")
Step = apps.get_model("workflows", "Step")
Edge = apps.get_model("workflows", "Edge")
Trigger = apps.get_model("workflows", "Trigger")
_TRIGGER_EXTENSION_FILTER_FIELDS = declared_hasura_resource_fields(Trigger, "hasura_filterable_fields")
_TRIGGER_EXTENSION_ORDER_FIELDS = declared_hasura_resource_fields(Trigger, "hasura_sortable_fields")
_TRIGGER_EXTENSION_GROUP_FIELDS = declared_hasura_resource_fields(Trigger, "hasura_groupable_fields")
_TRIGGER_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(Trigger, "hasura_insertable_fields")
_TRIGGER_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(Trigger, "hasura_updatable_fields")
_TRIGGER_EXTENSION_WRITE_FIELDS = tuple(
    dict.fromkeys((*_TRIGGER_EXTENSION_INSERT_FIELDS, *_TRIGGER_EXTENSION_UPDATE_FIELDS))
)
_TRIGGER_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name for name in _TRIGGER_EXTENSION_WRITE_FIELDS if Trigger._meta.get_field(name).is_relation
)
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
StepAttempt = apps.get_model("workflows", "StepAttempt")
StepArtifact = apps.get_model("workflows", "StepArtifact")
Decision = apps.get_model("workflows", "Decision")

_PROJECTED_STEP_ID = "_workflows_step_id"
_PROJECTED_STEP_WORKFLOW_NAME = "_workflows_step_workflow_name"
_PROJECTED_RUN_WORKFLOW_NAME = "_workflows_run_workflow_name"
_PROJECTED_STEP_NAME = "_workflows_step_name"
_PROJECTED_STEP_KEY = "_workflows_step_key"
_PROJECTED_SYSTEM_KIND = "_workflows_system_kind"
_PROJECTED_STEP_RUN_ID = "_workflows_step_run_id"


def _read_resource_queryset(model: type[models.Model]) -> Any:
    """Bind one read-only resource to the model's native REBAC read scope."""

    def get_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
        scoped = read_scoped_queryset(model, session_user(info), action="read")
        return model.objects.none() if scoped is None else scoped

    return get_queryset


def _artifact_queryset(info: strawberry.Info) -> models.QuerySet[Any]:
    """Scope artifact summaries through their independently readable attempts."""

    attempts = read_scoped_queryset(StepAttempt, session_user(info), action="read")
    if attempts is None:
        return StepArtifact.objects.none()
    return StepArtifact.objects.filter(attempt__in=attempts)


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
WorkflowTestScopeEnum = strawberry.enum(cast(Any, WorkflowScope), name="WorkflowTestScope")
WorkflowTestFixtureRoleEnum = strawberry.enum(cast(Any, FixtureRole), name="WorkflowTestFixtureRole")


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
    map_body_operation: bool

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
            map_body_operation=operation.map_body_operation,
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
    execution_actor: auto
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
            declaration = cast(Any, self).validated_config()
            if isinstance(declaration, EventTriggerConfig):
                publisher = next(
                    (
                        model
                        for model in GraphQLSchemas.from_discovery().change_publisher_models()
                        if model._meta.label_lower == declaration.model
                    ),
                    None,
                )
                if publisher is not None:
                    return declaration.summary_for(str(publisher._meta.verbose_name))
            return declaration.summary()
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
class WorkflowEventConditionField:
    name: str
    label: str
    scalar: str
    lookups: list[WorkflowEventConditionLookup]


@strawberry.type
class WorkflowEventConditionLookup:
    name: str
    key: str
    label: str
    value_schema: JSON


@strawberry.type
class WorkflowEventConditionClause:
    field: str
    lookup: str
    value: JSON | None
    source_key: str | None


@strawberry.input
class WorkflowEventConditionClauseInput:
    field: str
    lookup: str
    value: JSON | None
    source_key: str | None = None


@strawberry.type
class WorkflowEventConditionDraft:
    fields: list[WorkflowEventConditionField]
    clauses: list[WorkflowEventConditionClause]
    opaque: JSON
    condition: JSON | None
    errors: list[str]


@strawberry.type
class WorkflowSchedulePreview:
    timezone: str
    occurrences: list[datetime]
    errors: list[str]


@strawberry.input
class WorkflowTestFixtureInput:
    step_key: str
    role: FixtureRole
    item_index: int | None = None
    value: JSON | None = strawberry.UNSET
    outcome: str = ""
    captured_attempt: PublicID | None = None


@strawberry.type
class WorkflowTestPlanOperation:
    step_id: PublicID
    key: str
    label: str
    effect: StepEffect
    effect_description: str
    replaced_by_output: bool
    outcomes: list[WorkflowStepOutcome]


@strawberry.type
class WorkflowTestFixtureRequirement:
    role: FixtureRole
    step_id: PublicID
    step_key: str
    item_index_required: bool
    satisfied: bool


@strawberry.type
class WorkflowTestFreshness:
    code: str
    step_key: str | None
    field: str


@strawberry.type
class WorkflowTestPlan:
    revision: int
    scope: WorkflowScope
    source_step_id: PublicID | None
    snapshot_step_id: PublicID | None
    operations: list[WorkflowTestPlanOperation]
    required_fixtures: list[WorkflowTestFixtureRequirement]
    diagnostics: list[WorkflowDefinitionDiagnostic]
    requires_map_item: bool
    freshness: list[WorkflowTestFreshness]
    is_current: bool


@strawberry.type
class WorkflowTestFixtureSourceSummary:
    attempt_id: PublicID
    run_id: PublicID
    workflow_id: PublicID
    workflow_revision: int
    step_id: PublicID
    step_key: str
    role: FixtureRole
    item_index: int | None
    outcome: str
    recorded_at: datetime


@strawberry.type
class WorkflowTestFixtureSourcePage:
    items: list[WorkflowTestFixtureSourceSummary]
    next_after: PublicID | None


@strawberry.type
class WorkflowTestFixtureSource:
    summary: WorkflowTestFixtureSourceSummary
    value_present: bool
    value: JSON | None


@strawberry.type
class WorkflowRecoveryPlan:
    attempt_id: PublicID
    run_id: PublicID
    workflow_id: PublicID
    workflow_revision: int
    step_id: PublicID
    step_key: str
    map_index: int | None
    available: bool
    mode: str | None
    unavailable_reason: str


@strawberry.type
class WorkflowObjectReference:
    model: str
    id: PublicID


@strawberry.type
class WorkflowTestRepairContext:
    source_attempt_id: PublicID
    source_run_id: PublicID
    source_workflow_id: PublicID
    source_revision: int
    draft_workflow_id: PublicID
    draft_revision: int
    source_step_key: str
    source_step_id: PublicID
    current_source_step_id: PublicID | None
    subject: WorkflowObjectReference | None
    input_present: bool
    input: JSON | None
    fixtures: list[WorkflowTestFixtureSourceSummary]


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
        return [
            WorkflowTriggerPublisher(model=model._meta.label_lower, label=str(model._meta.verbose_name))
            for model in GraphQLSchemas.from_discovery().change_publisher_models()
        ]

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_event_condition_draft(
        self,
        model: str,
        condition: JSON | None,
        clauses: list[WorkflowEventConditionClauseInput] | None = None,
        opaque: JSON | None = None,
    ) -> WorkflowEventConditionDraft:
        """Decode or encode one prospective condition through its Django owner."""

        schemas = GraphQLSchemas.from_discovery()
        publisher = next(
            (candidate for candidate in schemas.change_publisher_models() if candidate._meta.label_lower == model),
            None,
        )
        if publisher is None:
            return WorkflowEventConditionDraft(
                fields=[],
                clauses=[],
                opaque=cast(JSON, {}),
                condition=condition,
                errors=["Event publisher is unavailable."],
            )
        readable = {
            field
            for schema_name in schemas.names()
            for resource in schemas.resources(schema_name)
            if resource.model is publisher
            for field in readable_model_field_names(resource)
        }
        catalogue = EventConditionCatalogue.from_model(publisher, readable_fields=readable)
        errors: list[str] = []
        projected_condition: object = condition
        if clauses is not None:
            if not isinstance(opaque, dict):
                errors.append("Opaque condition entries must be a JSON object.")
            else:
                try:
                    projected_condition = catalogue.encode(
                        [
                            EventConditionClause(item.field, item.lookup, item.value, item.source_key)
                            for item in clauses
                        ],
                        opaque=opaque,
                    )
                except ValidationError as error:
                    errors.extend(error.messages)
        decoded = catalogue.decode(projected_condition)
        errors.extend(decoded.errors)
        return WorkflowEventConditionDraft(
            fields=[
                WorkflowEventConditionField(
                    name=field.name,
                    label=field.label,
                    scalar=field.scalar,
                    lookups=[
                        WorkflowEventConditionLookup(
                            name=lookup.name,
                            key=lookup.key,
                            label=lookup.label,
                            value_schema=cast(JSON, lookup.value_schema),
                        )
                        for lookup in catalogue.authored_lookups(field.name)
                    ],
                )
                for field in catalogue.fields
            ],
            clauses=[
                WorkflowEventConditionClause(
                    field=clause.field,
                    lookup=clause.lookup,
                    value=cast(JSON, clause.value),
                    source_key=clause.source_key,
                )
                for clause in decoded.clauses
            ],
            opaque=cast(JSON, decoded.opaque),
            condition=cast(JSON, projected_condition),
            errors=errors,
        )

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_schedule_preview(self, config: JSON, count: int = 3) -> WorkflowSchedulePreview:
        result = schedule_draft_preview(config, now=timezone.now(), count=count)
        return WorkflowSchedulePreview(
            timezone="UTC",
            occurrences=list(result.occurrences),
            errors=list(result.errors),
        )


@strawberry.type
class WorkflowTestSetupQuery:
    """Thin GraphQL projection of model-owned workflow test setup rules."""

    @strawberry.field
    def workflow_test_plan(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        scope: WorkflowScope = cast(WorkflowScope, WorkflowScope.WHOLE),
        selected_step: PublicID | None = None,
        subject: WorkflowObjectRefInput | None = None,
        input: JSON | None = strawberry.UNSET,
        fixtures: list[WorkflowTestFixtureInput] | None = None,
        previous_run: PublicID | None = None,
    ) -> WorkflowTestPlan:
        target = instance_for_id(Workflow, workflow)
        if target is None:
            raise ValidationError({"workflow": "Workflow is unavailable."})
        selected = instance_for_id(Step, selected_step) if selected_step is not None else None
        previous = instance_for_id(WorkflowRun, previous_run) if previous_run is not None else None
        actor = session_user(info)
        plan = WorkflowRun.objects.test_setup_plan(
            target,
            expected_revision=expected_revision,
            actor=actor,
            subject=_resolve_subject(subject, actor=actor),
            input=JsonPresence(input is not strawberry.UNSET, None if input is strawberry.UNSET else input),
            scope=scope,
            selected_step=selected,
            fixtures=_test_fixture_specs(fixtures),
            previous_run=previous,
        )
        return _workflow_test_plan(expected_revision, scope, plan)

    @strawberry.field
    def workflow_test_fixture_sources(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        role: FixtureRole,
        step_key: str,
        item_index: int | None = None,
        after: PublicID | None = None,
        first: int = 20,
    ) -> WorkflowTestFixtureSourcePage:
        target = instance_for_id(Workflow, workflow)
        if target is None:
            raise ValidationError({"workflow": "Workflow is unavailable."})
        page = StepAttempt.objects.eligible_test_fixture_sources(
            target,
            actor=session_user(info),
            role=role,
            step_key=step_key,
            item_index=item_index,
            after=None if after is None else str(after),
            first=first,
        )
        return WorkflowTestFixtureSourcePage(
            items=[_test_fixture_source_summary(item) for item in page.items],
            next_after=None if page.next_after is None else PublicID(page.next_after),
        )

    @strawberry.field
    def workflow_test_fixture_source(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        attempt: PublicID,
        role: FixtureRole,
        step_key: str,
        item_index: int | None = None,
    ) -> WorkflowTestFixtureSource | None:
        target = instance_for_id(Workflow, workflow)
        if target is None:
            raise ValidationError({"workflow": "Workflow is unavailable."})
        source = StepAttempt.objects.test_fixture_source(
            target,
            actor=session_user(info),
            attempt_id=str(attempt),
            role=role,
            step_key=step_key,
            item_index=item_index,
        )
        if source is None:
            return None
        return WorkflowTestFixtureSource(
            summary=_test_fixture_source_summary(source.summary),
            value_present=source.value.present,
            value=cast(JSON | None, source.value.value),
        )

    @strawberry.field
    def workflow_recovery_plan(
        self,
        info: strawberry.Info,
        source_attempt: PublicID,
    ) -> WorkflowRecoveryPlan:
        attempt = instance_for_id(StepAttempt, source_attempt)
        if attempt is None:
            raise ValidationError({"source_attempt": "Recovery source evidence is unavailable."})
        plan = StepAttempt.objects.recovery_plan(attempt, actor=session_user(info))
        return WorkflowRecoveryPlan(
            attempt_id=PublicID(plan.attempt_id),
            run_id=PublicID(plan.run_id),
            workflow_id=PublicID(plan.workflow_id),
            workflow_revision=plan.workflow_revision,
            step_id=PublicID(plan.step_id),
            step_key=plan.step_key,
            map_index=plan.map_index,
            available=plan.capability.available,
            mode=None if plan.capability.mode is None else str(plan.capability.mode),
            unavailable_reason=plan.capability.unavailable_reason,
        )

    @strawberry.field
    def workflow_test_repair_context(
        self,
        info: strawberry.Info,
        source_attempt: PublicID,
    ) -> WorkflowTestRepairContext:
        attempt = instance_for_id(StepAttempt, source_attempt)
        if attempt is None:
            raise ValidationError({"source_attempt": "Repair source evidence is unavailable."})
        context = WorkflowRun.objects.test_repair_context(attempt, actor=session_user(info))
        subject = context.subject
        return WorkflowTestRepairContext(
            source_attempt_id=PublicID(context.source_attempt_id),
            source_run_id=PublicID(context.source_run_id),
            source_workflow_id=PublicID(context.source_workflow_id),
            source_revision=context.source_revision,
            draft_workflow_id=PublicID(context.draft_workflow_id),
            draft_revision=context.draft_revision,
            source_step_key=context.source_step_key,
            source_step_id=PublicID(context.source_step_id),
            current_source_step_id=(
                None if context.current_source_step_id is None else PublicID(context.current_source_step_id)
            ),
            subject=(
                None
                if subject is None
                else WorkflowObjectReference(
                    model=subject._meta.label,
                    id=cast(PublicID, to_public_id(type(subject), subject.pk)),
                )
            ),
            input_present=context.input.present,
            input=cast(JSON | None, context.input.value),
            fixtures=[_test_fixture_source_summary(item) for item in context.fixtures],
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
    occurrence_id: auto
    trigger: TriggerType | None
    parent_step_run: "StepRunType | None"
    recovery_source_attempt: "StepAttemptType | None"
    reprocessed_from: "WorkflowRunType | None"
    recovery_mode: auto
    test_repair_source_attempt: "StepAttemptType | None"
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
    current_attempt: "StepAttemptType | None"
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


@strawberry_django.type(StepAttempt)
class StepAttemptType(AngeeNode):
    """Read-only retained evidence for one logical workflow attempt."""

    step_run: StepRunType
    retry_of: "StepAttemptType | None"
    recovery_source_attempt: "StepAttemptType | None"
    recovery_mode: auto
    intended_effect_key: auto
    artifacts_present: auto
    retry_index: auto
    available_at: auto
    ordinal: auto
    cause: auto
    effect_generation: auto
    input_present: auto
    input: JSON | None
    input_provenance: JSON
    map_expansion: "StepAttemptType | None"
    map_item_index: auto
    map_item_present: auto
    map_item: JSON | None
    claimed_at: auto
    started_at: auto
    heartbeat_at: auto
    lease_revoked_at: auto
    lease_revocation_reason: auto
    result_kind: auto
    result_recorded_at: auto
    output_present: auto
    output: JSON | None
    checkpoint_present: auto
    checkpoint: JSON | None
    error: auto
    stacktrace: auto
    outcome: auto
    waiting_kind: auto
    orchestration_error: auto
    applied_at: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(
        only=["result_recorded_at", "applied_at", "lease_revoked_at", "started_at", "claimed_at"]
    )
    def status(self) -> str:
        """Return the lifecycle derived by the retained-attempt model owner."""

        return str(cast(Any, self).status)


@strawberry.type
class WorkflowArtifactTarget:
    model: str
    id: PublicID


@strawberry_django.type(StepArtifact)
class StepArtifactType(AngeeNode):
    """A payload-free retained artifact summary for one selected attempt."""

    attempt: StepAttemptType
    declaration_index: auto
    label: auto
    created_at: auto

    @strawberry_django.field(only=["target_content_type_id", "target_object_id"])
    def target_reference(self, info: strawberry.Info) -> WorkflowArtifactTarget | None:
        target = cast(Any, self).target
        if target is None:
            return None
        scoped = read_scoped_queryset(type(target), session_user(info), action="read")
        if scoped is None or not scoped.filter(pk=target.pk).exists():
            return None
        return WorkflowArtifactTarget(
            model=target._meta.label,
            id=cast(PublicID, to_public_id(type(target), target.pk)),
        )


@strawberry_django.type(Decision)
class DecisionType(AngeeNode):
    """Admin projection of one awaited workflow decision."""

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

    @strawberry_django.field(only=["step_run_id"])
    def step_run(self, info: strawberry.Info) -> StepRunType | None:
        """Return the journal row only when it is independently readable."""

        step_run_id = cast(Any, self).step_run_id
        scoped = read_scoped_queryset(StepRun, session_user(info), action="read")
        if scoped is None:
            return None
        return cast(StepRunType | None, scoped.filter(pk=step_run_id).first())


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

    @strawberry_django.field(only=["step_run__run_id"])
    def source_run_id(self, info: strawberry.Info) -> PublicID | None:
        """Return the source Run only when the caller may read that journal."""

        run_id = cast(Any, self).step_run.run_id
        scoped = read_scoped_queryset(WorkflowRun, session_user(info), action="read")
        if scoped is None or not scoped.filter(pk=run_id).exists():
            return None
        return to_public_id(WorkflowRun, run_id)

    @strawberry_django.field(only=["step_run_id"])
    def source_execution_id(self, info: strawberry.Info) -> PublicID | None:
        """Return the source StepRun only when the caller may read that journal."""

        step_run_id = cast(Any, self).step_run_id
        scoped = read_scoped_queryset(StepRun, session_user(info), action="read")
        if scoped is None or not scoped.filter(pk=step_run_id).exists():
            return None
        return to_public_id(StepRun, step_run_id)

    @strawberry_django.field(only=["suspension_attempt_id"])
    def source_attempt_id(self, info: strawberry.Info) -> PublicID | None:
        """Return the suspension Attempt only when the caller may read its journal."""

        attempt_id = cast(Any, self).suspension_attempt_id
        if attempt_id is None:
            return None
        scoped = read_scoped_queryset(StepAttempt, session_user(info), action="read")
        if scoped is None or not scoped.filter(pk=attempt_id).exists():
            return None
        return to_public_id(StepAttempt, attempt_id)

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
class WorkflowDefinitionChange:
    kind: str
    change: str
    key: str
    field: str | None
    before: JSON | None
    after: JSON | None
    presentation_only: bool


@strawberry.type
class WorkflowDefinitionChangeCounts:
    steps_added: int
    steps_removed: int
    steps_changed: int
    connections_added: int
    connections_removed: int
    settings_changed: int


@strawberry.type
class WorkflowDefinitionComparisonNode:
    key: str
    name: str
    step_class: str
    is_entry: bool


@strawberry.type
class WorkflowDefinitionComparisonEdge:
    source: str
    target: str
    condition: str


@strawberry.type
class WorkflowDefinitionComparison:
    source_id: PublicID
    source_version: int
    source_status: str
    draft_id: PublicID
    draft_revision: int
    counts: WorkflowDefinitionChangeCounts
    changes: list[WorkflowDefinitionChange]
    source_nodes: list[WorkflowDefinitionComparisonNode]
    source_edges: list[WorkflowDefinitionComparisonEdge]
    draft_nodes: list[WorkflowDefinitionComparisonNode]
    draft_edges: list[WorkflowDefinitionComparisonEdge]


@strawberry.type
class WorkflowDefinitionRestorePayload:
    status: WorkflowDefinitionStatus
    current_revision: int | None = None
    snapshot: WorkflowDefinitionSnapshot | None = None


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


@strawberry.type
class WorkflowMapBodyCandidate:
    id: PublicID | None
    client_key: str | None
    step_key: str
    label: str
    eligible: bool
    reason: str | None


@strawberry.type
class WorkflowMapBodyCandidatesPayload:
    status: WorkflowDefinitionStatus
    revision: int | None = None
    current_revision: int | None = None
    candidates: list[WorkflowMapBodyCandidate] = strawberry.field(default_factory=list)
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
    filterable=["id", "workflow", "execution_actor", "kind", "enabled", "next_fire_at", "updated_at", *_TRIGGER_EXTENSION_FILTER_FIELDS],
    sortable=["workflow", "kind", "enabled", "next_fire_at", "created_at", "updated_at", *_TRIGGER_EXTENSION_ORDER_FIELDS],
    aggregatable=["id"],
    groupable=["workflow", "workflow__name", "kind", "enabled", "updated_at", *_TRIGGER_EXTENSION_GROUP_FIELDS],
    insertable=["workflow", "execution_actor", "kind", "config", *_TRIGGER_EXTENSION_INSERT_FIELDS],
    updatable=["execution_actor", "kind", "config", *_TRIGGER_EXTENSION_UPDATE_FIELDS],
    field_id_decode={
        "workflow": public_pk_decoder(Workflow),
        "execution_actor": public_pk_decoder(User),
        **{
            name: public_pk_decoder(Trigger._meta.get_field(name).related_model)
            for name in _TRIGGER_EXTENSION_PUBLIC_ID_FIELDS
        },
    },
    get_queryset=_trigger_queryset,
    write_backend=AngeeHasuraWriteBackend(
        Trigger,
        public_id_fields=("workflow", "execution_actor", *_TRIGGER_EXTENSION_PUBLIC_ID_FIELDS),
    ),
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
        "reprocessed_from",
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
    get_queryset=_read_resource_queryset(WorkflowRun),
    field_id_decode={
        "workflow": public_pk_decoder(Workflow),
        "workflow__published_from": public_pk_decoder(Workflow),
        "trigger": public_pk_decoder(Trigger),
        "reprocessed_from": public_pk_decoder(WorkflowRun),
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
    get_queryset=_read_resource_queryset(StepRun),
    field_id_decode={
        "run": public_pk_decoder(WorkflowRun),
        "step": public_pk_decoder(Step),
    },
)
_STEP_ATTEMPT_RESOURCE = hasura_model_resource(
    StepAttemptType,
    model=StepAttempt,
    name="workflow_step_attempts",
    filterable=[
        "id",
        "step_run",
        "retry_of",
        "cause",
        "result_kind",
        "map_expansion",
        "ordinal",
        "updated_at",
    ],
    sortable=[
        "step_run",
        "ordinal",
        "available_at",
        "claimed_at",
        "started_at",
        "result_recorded_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "ordinal"],
    groupable=["step_run", "cause", "result_kind", "outcome"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=_read_resource_queryset(StepAttempt),
    field_id_decode={
        "step_run": public_pk_decoder(StepRun),
        "retry_of": public_pk_decoder(StepAttempt),
        "map_expansion": public_pk_decoder(StepAttempt),
    },
)
_STEP_ARTIFACT_RESOURCE = hasura_model_resource(
    StepArtifactType,
    model=StepArtifact,
    name="workflow_step_artifacts",
    filterable=["id", "attempt", "declaration_index", "label", "created_at"],
    sortable=["attempt", "declaration_index", "created_at"],
    aggregatable=["id", "declaration_index"],
    groupable=["attempt"],
    insert=False,
    update=False,
    delete=False,
    get_queryset=_artifact_queryset,
    field_id_decode={"attempt": public_pk_decoder(StepAttempt)},
)
_DECISION_RESOURCE = hasura_model_resource(
    DecisionType,
    model=Decision,
    name="workflow_decisions",
    filterable=[
        "id",
        "step_run",
        "step_run__run",
        "suspension_attempt",
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
    get_queryset=_read_resource_queryset(Decision),
    field_id_decode={
        "step_run": public_pk_decoder(StepRun),
        "step_run__run": public_pk_decoder(WorkflowRun),
        "suspension_attempt": public_pk_decoder(StepAttempt),
    },
)
_PUBLIC_DECISION_RESOURCE = hasura_model_resource(
    PublicDecisionType,
    model=Decision,
    name="workflow_decisions",
    filterable=[
        "id",
        "step_run",
        "step_run__run",
        "suspension_attempt",
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
    get_queryset=_read_resource_queryset(Decision),
    field_id_decode={
        "step_run": public_pk_decoder(StepRun),
        "step_run__run": public_pk_decoder(WorkflowRun),
        "suspension_attempt": public_pk_decoder(StepAttempt),
    },
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
        workflows = cast(Any, scoped).for_subject_declaration(subject_declaration).with_lineage_projection()
        return cast(list[WorkflowType], workflows)

    @strawberry.field
    def workflow_runs_for_subject(
        self, info: strawberry.Info, subject: WorkflowObjectRefInput,
    ) -> list[WorkflowRunType]:
        """Return actor-readable native run history for one readable record."""

        actor = session_user(info)
        try:
            model = cast(type[models.Model], apps.get_model(subject.subject_declaration))
        except (LookupError, ValueError):
            return []
        target_scope = read_scoped_queryset(model, actor)
        if target_scope is None:
            return []
        target = instance_for_id(model, subject.id, queryset=target_scope)
        if target is None:
            return []
        content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)
        runs = read_scoped_queryset(cast(type[models.Model], WorkflowRun), actor)
        if runs is None:
            return []
        artifact_content_type, artifact_object_id = canonical_record_target(target)
        artifact_runs = apps.get_model("workflows", "StepArtifact")._base_manager.filter(
            target_content_type=artifact_content_type, target_object_id=artifact_object_id,
        ).values("attempt__step_run__run_id")
        return cast(list[WorkflowRunType], runs.filter(
            models.Q(subject_content_type=content_type, subject_object_id=target.pk)
            | models.Q(pk__in=models.Subquery(artifact_runs)),
        ).select_related("workflow").distinct().order_by("-created_at", "-pk"))


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
            return ActionResult.from_error(error, "Publishing the workflow failed.")
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

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def restore_workflow_definition(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        source_workflow: Annotated[PublicID, strawberry.argument(name="source")],
        expected_revision: int,
    ) -> WorkflowDefinitionRestorePayload:
        """Restore an exact immutable publication into the saved draft."""

        draft = authorized_action_target(info, Workflow, workflow, "write")
        version = authorized_action_target(info, Workflow, source_workflow, "read")
        try:
            result = Workflow.objects.restore_definition(
                draft, version, expected_revision=expected_revision
            )
        except StaleDefinitionError as error:
            return WorkflowDefinitionRestorePayload(
                status=WorkflowDefinitionStatus.STALE,
                current_revision=error.current,
            )
        return WorkflowDefinitionRestorePayload(
            status=WorkflowDefinitionStatus.SUCCESS,
            current_revision=result.snapshot.revision,
            snapshot=_definition_snapshot(result.snapshot),
        )


@strawberry.type
class WorkflowDefinitionQuery:
    """Coherent workflow definition reads for the console editor."""

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_definition(self, info: strawberry.Info, workflow: PublicID) -> WorkflowDefinitionSnapshot:
        target = authorized_action_target(info, Workflow, workflow, "read")
        snapshot = Workflow.objects.definition_snapshot(target)
        return _definition_snapshot(snapshot)

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_definition_comparison(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        source_workflow: Annotated[PublicID, strawberry.argument(name="source")],
    ) -> WorkflowDefinitionComparison:
        """Compare an exact immutable publication with the latest saved draft."""

        draft = authorized_action_target(info, Workflow, workflow, "read")
        version = authorized_action_target(info, Workflow, source_workflow, "read")
        return _definition_comparison(Workflow.objects.compare_definition(draft, version))

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

    @strawberry.field(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def workflow_map_body_candidates(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        edit: WorkflowDefinitionEditInput,
        owner: WorkflowEndpointInput,
    ) -> WorkflowMapBodyCandidatesPayload:
        """Project Map-body choices from the caller's exact unsaved definition."""

        target = authorized_action_target(info, Workflow, workflow, "write")
        try:
            result = Workflow.objects.definition_map_body_candidates(
                target,
                expected_revision=expected_revision,
                edit=_definition_edit(target, edit),
                owner=_endpoint_ref(target, owner),
            )
        except StaleDefinitionError as error:
            return WorkflowMapBodyCandidatesPayload(
                status=WorkflowDefinitionStatus.STALE,
                current_revision=error.current,
            )
        except DefinitionEditError as error:
            return WorkflowMapBodyCandidatesPayload(
                status=WorkflowDefinitionStatus.STRUCTURAL,
                revision=expected_revision,
                diagnostics=_definition_diagnostics(error.diagnostics),
            )
        return WorkflowMapBodyCandidatesPayload(
            status=WorkflowDefinitionStatus.SUCCESS,
            revision=result.revision,
            candidates=[
                WorkflowMapBodyCandidate(
                    id=(
                        cast(PublicID, to_public_id(Step, item.identity.existing_id))
                        if item.identity.existing_id is not None else None
                    ),
                    client_key=item.identity.client_key,
                    step_key=item.key,
                    label=item.label,
                    eligible=item.eligible,
                    reason=item.reason,
                )
                for item in result.candidates
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
    except (TypeError, ValueError, ValidationError):
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


def _definition_snapshot(snapshot: Any) -> WorkflowDefinitionSnapshot:
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


def _definition_comparison(result: Any) -> WorkflowDefinitionComparison:
    changes = [
        WorkflowDefinitionChange(
            kind=item.kind,
            change=item.change,
            key=item.key,
            field=item.field,
            before=cast(JSON | None, item.before),
            after=cast(JSON | None, item.after),
            presentation_only=item.presentation_only,
        )
        for item in result.changes
    ]
    changed_steps = {item.key for item in result.changes if item.kind == "step" and item.change == "changed"}
    return WorkflowDefinitionComparison(
        source_id=result.source.sqid,
        source_version=result.source.version,
        source_status=str(result.source.status),
        draft_id=result.draft.sqid,
        draft_revision=result.draft_revision,
        counts=WorkflowDefinitionChangeCounts(
            steps_added=sum(item.kind == "step" and item.change == "added" for item in result.changes),
            steps_removed=sum(item.kind == "step" and item.change == "removed" for item in result.changes),
            steps_changed=len(changed_steps),
            connections_added=sum(
                item.kind == "connection" and item.change == "added" for item in result.changes
            ),
            connections_removed=sum(
                item.kind == "connection" and item.change == "removed" for item in result.changes
            ),
            settings_changed=sum(item.kind == "settings" for item in result.changes),
        ),
        changes=changes,
        source_nodes=_comparison_nodes(result.source_definition),
        source_edges=_comparison_edges(result.source_definition),
        draft_nodes=_comparison_nodes(result.draft_definition),
        draft_edges=_comparison_edges(result.draft_definition),
    )


def _comparison_nodes(definition: dict[str, Any]) -> list[WorkflowDefinitionComparisonNode]:
    return [
        WorkflowDefinitionComparisonNode(
            key=item["key"],
            name=item["name"],
            step_class=item["step_class"],
            is_entry=item["is_entry"],
        )
        for item in definition["steps"]
    ]


def _comparison_edges(definition: dict[str, Any]) -> list[WorkflowDefinitionComparisonEdge]:
    return [
        WorkflowDefinitionComparisonEdge(
            source=item["source"],
            target=item["target"],
            condition=item["condition"],
        )
        for item in definition["edges"]
    ]


def _test_fixture_specs(values: list[WorkflowTestFixtureInput] | None) -> tuple[FixtureSpec, ...]:
    return tuple(
        FixtureSpec(
            step_key=item.step_key,
            role=item.role,
            item_index=item.item_index,
            value=JsonPresence(
                item.value is not strawberry.UNSET,
                None if item.value is strawberry.UNSET else item.value,
            ),
            outcome=item.outcome,
            captured_attempt_id=(None if item.captured_attempt is None else str(item.captured_attempt)),
        )
        for item in values or ()
    )


def _step_identity(value: GraphIdentity) -> PublicID:
    if value.existing_id is None:
        raise ValueError("Persisted workflow test plans require saved step identities.")
    return cast(PublicID, to_public_id(Step, value.existing_id))


def _workflow_test_plan(revision: int, scope: WorkflowScope, plan: Any) -> WorkflowTestPlan:
    return WorkflowTestPlan(
        revision=revision,
        scope=scope,
        source_step_id=None if plan.source_step_id is None else PublicID(plan.source_step_id),
        snapshot_step_id=None if plan.snapshot_step_id is None else PublicID(plan.snapshot_step_id),
        operations=[
            WorkflowTestPlanOperation(
                step_id=_step_identity(item.identity),
                key=item.key,
                label=item.label,
                effect=item.effect,
                effect_description=item.effect_description,
                replaced_by_output=item.replaced_by_output,
                outcomes=[
                    WorkflowStepOutcome(
                        key=outcome.key,
                        label=outcome.label,
                        description=outcome.description,
                    )
                    for outcome in item.outcomes
                ],
            )
            for item in plan.operations
        ],
        required_fixtures=[
            WorkflowTestFixtureRequirement(
                role=item.role,
                step_id=_step_identity(item.identity),
                step_key=item.step_key,
                item_index_required=item.item_index_required,
                satisfied=item.satisfied,
            )
            for item in plan.required_fixtures
        ],
        diagnostics=_definition_diagnostics(plan.diagnostics),
        requires_map_item=plan.requires_map_item,
        freshness=[
            WorkflowTestFreshness(code=item.code, step_key=item.step_key, field=item.field)
            for item in plan.freshness
        ],
        is_current=plan.is_current,
    )


def _test_fixture_source_summary(item: Any) -> WorkflowTestFixtureSourceSummary:
    return WorkflowTestFixtureSourceSummary(
        attempt_id=PublicID(item.attempt_id),
        run_id=PublicID(item.run_id),
        workflow_id=PublicID(item.workflow_id),
        workflow_revision=item.workflow_revision,
        step_id=PublicID(item.step_id),
        step_key=item.step_key,
        role=item.role,
        item_index=item.item_index,
        outcome=item.outcome,
        recorded_at=item.recorded_at,
    )


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
        with actor_context(actor):
            target = authorized_action_target(info, Decision, decision, "act")
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
        with actor_context(actor):
            target = authorized_action_target(info, Decision, decision, "act")
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
    @action_guard("Reprocess workflow run failed.")
    def reprocess_workflow_run(
        self, info: strawberry.Info, run: PublicID, request_key: str
    ) -> ActionResult:
        """Start one idempotent new run against the current published lineage."""

        actor = session_user(info)
        source = authorized_action_target(info, WorkflowRun, run, "write")
        result = WorkflowRun.objects.reprocess(source, actor=actor, request_key=request_key)
        return ActionResult(ok=True, message=f"Started workflow run {result.sqid}.", id=result.sqid)

    @strawberry.mutation
    @action_guard("Test workflow failed.")
    def start_workflow_test(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        expected_revision: int,
        request_key: str,
        scope: WorkflowScope = cast(WorkflowScope, WorkflowScope.WHOLE),
        source_step: PublicID | None = None,
        subject: WorkflowObjectRefInput | None = None,
        input: JSON | None = strawberry.UNSET,
        fixtures: list[WorkflowTestFixtureInput] | None = None,
        repair_source_attempt: PublicID | None = None,
    ) -> ActionResult:
        """Start or recover a whole-workflow test of one saved revision."""

        actor = session_user(info)
        target = authorized_action_target(info, Workflow, workflow, "write")
        selected = instance_for_id(Step, source_step) if source_step is not None else None
        repair_source = (
            instance_for_id(StepAttempt, repair_source_attempt)
            if repair_source_attempt is not None
            else None
        )
        if repair_source_attempt is not None and repair_source is None:
            raise ValidationError({"repair_source_attempt": "Test repair source evidence is unavailable."})
        run = WorkflowRun.objects.start_test(
            target,
            expected_revision=expected_revision,
            request_key=request_key,
            subject=_resolve_subject(subject, actor=actor),
            actor=actor,
            input=JsonPresence(input is not strawberry.UNSET, None if input is strawberry.UNSET else input),
            scope=scope,
            selected_step=selected,
            fixtures=_test_fixture_specs(fixtures),
            repair_source_attempt=repair_source,
        )
        return ActionResult(ok=True, message=f"Started workflow test {run.sqid}.", id=run.sqid)

    @strawberry.mutation
    @action_guard("Start workflow recovery failed.")
    def start_workflow_recovery(
        self,
        info: strawberry.Info,
        source_attempt: PublicID,
        request_key: str,
    ) -> ActionResult:
        """Start or recover one idempotent run from exact retained failure evidence."""

        attempt = instance_for_id(StepAttempt, source_attempt)
        if attempt is None:
            raise ValidationError({"source_attempt": "Recovery source evidence is unavailable."})
        run = WorkflowRun.objects.start_recovery(
            attempt,
            request_key=request_key,
            actor=session_user(info),
        )
        return ActionResult(ok=True, message=f"Started workflow recovery {run.sqid}.", id=run.sqid)

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
    WorkflowTestScopeEnum,
    WorkflowTestFixtureRoleEnum,
    WorkflowStepOutcome,
    WorkflowStepOperation,
    WorkflowType,
    StepType,
    EdgeType,
    TriggerType,
    WorkflowRunType,
    StepRunType,
    StepAttemptType,
    StepArtifactType,
    WorkflowArtifactTarget,
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
    WorkflowDefinitionChange,
    WorkflowDefinitionChangeCounts,
    WorkflowDefinitionComparisonNode,
    WorkflowDefinitionComparisonEdge,
    WorkflowDefinitionComparison,
    WorkflowDefinitionRestorePayload,
    WorkflowTestFixtureInput,
    WorkflowTestPlanOperation,
    WorkflowTestFixtureRequirement,
    WorkflowTestFreshness,
    WorkflowTestPlan,
    WorkflowTestFixtureSourceSummary,
    WorkflowTestFixtureSourcePage,
    WorkflowTestFixtureSource,
    WorkflowRecoveryPlan,
    WorkflowObjectReference,
    WorkflowTestRepairContext,
    *_WORKFLOW_RESOURCE.types,
    *_STEP_RESOURCE.types,
    *_EDGE_RESOURCE.types,
    *_TRIGGER_RESOURCE.types,
    *_WORKFLOW_RUN_RESOURCE.types,
    *_STEP_RUN_RESOURCE.types,
    *_STEP_ATTEMPT_RESOURCE.types,
    *_STEP_ARTIFACT_RESOURCE.types,
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
            WorkflowTestSetupQuery,
            WorkflowDefinitionQuery,
            _WORKFLOW_RESOURCE.query,
            _STEP_RESOURCE.query,
            _EDGE_RESOURCE.query,
            _TRIGGER_RESOURCE.query,
            _WORKFLOW_RUN_RESOURCE.query,
            _STEP_RUN_RESOURCE.query,
            _STEP_ATTEMPT_RESOURCE.query,
            _STEP_ARTIFACT_RESOURCE.query,
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
