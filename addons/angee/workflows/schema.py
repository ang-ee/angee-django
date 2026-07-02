"""GraphQL schema contributions for the workflows addon."""

from __future__ import annotations

from typing import cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac import system_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_target, resolve_action_target
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


@strawberry_django.type(Workflow)
class WorkflowType(AngeeNode):
    """Admin projection of a workflow definition."""

    name: auto
    description: auto
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
    artifact_object_id: auto
    data: JSON
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
    wait_event: auto
    heartbeat_at: auto
    error: auto
    stacktrace: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Decision)
class DecisionType(AngeeNode):
    """Public projection of one awaited workflow decision."""

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


@strawberry.input
class WorkflowObjectRefInput:
    """Generic subject reference for starting a workflow run."""

    app_label: str
    model: str
    id: PublicID


_WORKFLOW_RESOURCE = hasura_model_resource(
    WorkflowType,
    model=Workflow,
    name="workflows",
    filterable=["id", "name", "status", "version", "published_from", "error_workflow", "updated_at"],
    sortable=["name", "status", "version", "created_at", "updated_at"],
    aggregatable=["id", "version", "max_steps"],
    groupable=["status", "updated_at"],
    insertable=["name", "description", "error_workflow", "max_steps", "budget"],
    updatable=["name", "description", "error_workflow", "max_steps", "budget"],
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


@strawberry.type
class DecisionMutation:
    """Public decision resolution mutation."""

    @strawberry.mutation
    def decide(
        self,
        info: strawberry.Info,
        decision: PublicID,
        verdict: str,
        payload: JSON | None = None,
    ) -> DecisionType:
        """Resolve one pending decision as the signed-in session actor."""

        actor = session_user(info)
        target = resolve_action_target(Decision, decision, reason="workflows.graphql.decide")
        return cast(DecisionType, engine.decide(target, verdict, payload=payload, actor=actor))


@strawberry.type
class WorkflowRunActionMutation:
    """Console actions for workflow run lifecycle."""

    @strawberry.mutation(permission_classes=_ADMIN_PERMISSION_CLASSES)
    def start_workflow_run(
        self,
        info: strawberry.Info,
        workflow: PublicID,
        subject: WorkflowObjectRefInput | None = None,
    ) -> ActionResult:
        """Start the current published version of a workflow lineage."""

        target = resolve_action_target(Workflow, workflow, reason="workflows.graphql.start_workflow_run")
        try:
            run = engine.start(
                target,
                subject=_resolve_subject(subject),
                actor=getattr(info.context.request, "user", None),
            )
        except Exception as error:  # noqa: BLE001 - domain start failures return action results.
            return ActionResult(ok=False, message=f"Start failed: {error}")
        return ActionResult(ok=True, message=f"Started workflow run {run.sqid}.")

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
            resolve_action_target(Step, step_id, reason="workflows.graphql.override_run.step")
            for step_id in next_steps
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
    DecisionType,
    *_DECISION_RESOURCE.types,
]

schemas = {
    "public": {
        "query": [_DECISION_RESOURCE.query],
        "mutation": [DecisionMutation],
        "subscription": [changes(Decision, field="decisionChanged")],
        "types": _PUBLIC_TYPES,
    },
    "console": {
        "query": [
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
            WorkflowRunActionMutation,
            TriggerActionMutation,
            DecisionMutation,
        ],
        "subscription": [
            changes(WorkflowRun, field="workflowRunChanged"),
            changes(Decision, field="decisionChanged"),
        ],
        "types": _CONSOLE_TYPES,
    },
}
"""GraphQL contributions installed by the workflows addon."""


def _resolve_subject(ref: WorkflowObjectRefInput | None) -> models.Model | None:
    """Resolve an optional generic subject reference from GraphQL input."""

    if ref is None:
        return None
    model = apps.get_model(ref.app_label, ref.model)
    with system_context(reason="workflows.graphql.subject"):
        return cast(models.Model | None, instance_for_id(cast(type[models.Model], model), ref.id))
