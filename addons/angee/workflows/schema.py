"""GraphQL schema contributions for the workflows addon."""

from __future__ import annotations

from enum import Enum
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.models import read_scoped_queryset
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    action_target,
    authorized_action_target,
    resolve_action_target,
)
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES as _ADMIN_PERMISSION_CLASSES
from angee.iam.permissions import session_user
from angee.workflows import engine

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


@strawberry_django.type(Workflow)
class WorkflowType(AngeeNode):
    """Admin projection of a workflow definition."""

    name: auto
    description: auto
    subject_declaration: auto
    status: auto
    version: auto
    published_from: "WorkflowType | None"
    error_workflow: "WorkflowType | None"
    max_steps: auto
    budget: JSON
    created_at: auto
    updated_at: auto


@strawberry_django.type(Step)
class StepType(AngeeNode):
    """Admin projection of a workflow step definition."""

    workflow: WorkflowType
    key: auto
    name: auto
    step_class: auto
    config: JSON
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
    created_at: auto
    updated_at: auto


@strawberry_django.type(WorkflowRun)
class WorkflowRunType(AngeeNode):
    """Admin projection of a workflow run."""

    workflow: WorkflowType
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


_WORKFLOW_RESOURCE = hasura_model_resource(
    WorkflowType,
    model=Workflow,
    name="workflows",
    filterable=[
        "id",
        "name",
        "subject_declaration",
        "status",
        "version",
        "published_from",
        "error_workflow",
        "updated_at",
    ],
    sortable=["name", "status", "version", "created_at", "updated_at"],
    aggregatable=["id", "version", "max_steps"],
    groupable=["status", "updated_at"],
    insertable=["name", "description", "subject_declaration", "error_workflow", "max_steps", "budget"],
    updatable=["name", "description", "subject_declaration", "error_workflow", "max_steps", "budget"],
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
    insertable=["workflow", "key", "name", "step_class", "config", "join_rule", "is_entry", "position"],
    updatable=["key", "name", "step_class", "config", "join_rule", "is_entry", "position"],
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
    insertable=["workflow", "kind", "enabled", "config"],
    updatable=["kind", "enabled", "config"],
    field_id_decode={"workflow": public_pk_decoder(Workflow)},
    write_backend=AngeeHasuraWriteBackend(Trigger, public_id_fields=("workflow",)),
)
_WORKFLOW_RUN_RESOURCE = hasura_model_resource(
    WorkflowRunType,
    model=WorkflowRun,
    name="workflow_runs",
    filterable=[
        "id",
        "workflow",
        "trigger",
        "parent_step_run",
        "status",
        "wake_at",
        "updated_at",
    ],
    sortable=["workflow", "status", "wake_at", "steps_taken", "created_at", "updated_at"],
    aggregatable=["id", "steps_taken"],
    groupable=["workflow", "workflow__name", "status", "updated_at"],
    insert=False,
    update=False,
    delete=False,
    field_id_decode={
        "workflow": public_pk_decoder(Workflow),
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
        "updated_at",
    ],
    sortable=["run", "step", "map_index", "status", "attempt", "created_at", "updated_at"],
    aggregatable=["id", "attempt"],
    groupable=["run", "step", "step__key", "system_kind", "status", "outcome", "updated_at"],
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
        except Exception as error:  # noqa: BLE001 - domain publish failures return action results.
            return ActionResult(ok=False, message=f"Publish failed: {error}")
        return ActionResult(ok=True, message=f"Published workflow {published.sqid}.")


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
    WorkflowType,
    StepType,
    EdgeType,
    TriggerType,
    WorkflowRunType,
    StepRunType,
    DecisionType,
    WorkflowObjectRefInput,
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
