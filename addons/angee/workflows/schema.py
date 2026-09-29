"""Read-only execution resources and manager-backed operator actions."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto
from strawberry.scalars import JSON

from angee.decisions.schema import DecisionGroupType
from angee.graphql.actions import (
    ActionResult,
    action_guard,
    authorized_action_target,
    authorized_permission_target,
)
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_many, actor_scoped_to_one
from angee.iam.identity import user_public_id
from angee.iam.permissions import request_from_info
from angee.iam.schema import UserType
from angee.workflows.states import RunOrigin

strawberry.enum(cast(Any, RunOrigin))

Workflow = apps.get_model("workflows", "Workflow")
WorkflowVersion = apps.get_model("workflows", "WorkflowVersion")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
StepAttempt = apps.get_model("workflows", "StepAttempt")
StepArtifact = apps.get_model("workflows", "StepArtifact")
DecisionGroup = apps.get_model("decisions", "DecisionGroup")


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

    number: auto
    document: JSON
    content_hash: auto
    created_at: auto
    published_by: UserType | None = actor_scoped_to_one("published_by")
    workflow: WorkflowType | None = actor_scoped_to_one("workflow")


@strawberry_django.type(WorkflowRun)
class WorkflowRunType(AngeeNode):
    """Execution state, admission and result visible through the run's policy."""

    version: WorkflowVersionType | None = actor_scoped_to_one("version")
    reprocess_of: WorkflowRunType | None = actor_scoped_to_one("reprocess_of")
    step_runs: list[StepRunType] = actor_scoped_to_many("step_runs")
    run_as: UserType | None = actor_scoped_to_one("run_as")
    status: auto
    input: JSON
    output: JSON
    outcome: auto
    error: auto
    request_key: auto
    created_at: auto
    updated_at: auto
    finished_at: auto

    @strawberry_django.field(only=["reprocess_of_id"])
    def origin(self) -> RunOrigin:
        """Read the model's derived admission origin."""
        return cast(Any, self).origin

    @strawberry_django.field(only=["status"])
    def can_cancel(self, info: strawberry.Info) -> bool:
        """Project the model's viewer-specific cancellation predicate."""
        return bool(cast(Any, self).can_cancel(request_from_info(info).user))

    @strawberry_django.field(only=["status", "version_id"])
    def can_reprocess(self, info: strawberry.Info) -> bool:
        """Project the model's viewer-specific replacement admission predicate."""
        return bool(cast(Any, self).can_reprocess(request_from_info(info).user))

    @strawberry_django.field(only=["subject_content_type_id", "subject_object_id"])
    def subject_model(self) -> str:
        """Return the referenced model without loading the subject record."""
        return cast(Any, self).record_model_label

    @strawberry_django.field(only=["subject_content_type_id", "subject_object_id"])
    def subject_id(self) -> PublicID | None:
        """Return the subject's public identity; navigation rechecks its policy."""
        return optional_public_id(cast(Any, self).record_public_id or None)


@strawberry_django.type(StepRun)
class StepRunType(AngeeNode):
    """A node's retained execution and wait state; transitions use actions."""

    run: WorkflowRunType | None = actor_scoped_to_one("run")
    decision_group: DecisionGroupType | None = actor_scoped_to_one("decision_group")
    attempts: list[StepAttemptType] = actor_scoped_to_many("attempts")
    artifacts: list[StepArtifactType] = actor_scoped_to_many("artifacts")
    node_key: auto
    rank: auto
    map_index: auto
    is_mapped: bool = strawberry_django.field(only=["node_key"])
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


@strawberry_django.type(StepAttempt)
class StepAttemptType(AngeeNode):
    """One claim's timing, effect marker, failure and operator acknowledgment."""

    step_run: StepRunType | None = actor_scoped_to_one("step_run")
    number: auto
    started_at: auto
    finished_at: auto
    result: auto
    error: auto
    stacktrace: auto
    effect_started_at: auto

    @strawberry_django.field(only=["acknowledged_by_id"])
    def acknowledged_by(self) -> PublicID | None:
        """Return the operator's public identity without exposing a user row."""
        return optional_public_id(user_public_id(cast(Any, self).acknowledged_by_id))


@strawberry_django.type(StepArtifact)
class StepArtifactType(AngeeNode):
    """A step's labeled reference to a record, governed by its execution policy."""

    step_run: StepRunType | None = actor_scoped_to_one("step_run")
    label: auto
    created_at: auto

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def model_label(self) -> str:
        """Project target identity through the shared record-reference owner."""
        return cast(Any, self).record_model_label

    @strawberry_django.field(only=["content_type_id", "object_id"])
    def record_id(self) -> PublicID:
        """Return the artifact target's public id; navigation rechecks its policy."""
        return PublicID(cast(Any, self).record_public_id)


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
    filterable=["id", "version", "version__workflow", "run_as", "status", "outcome", "reprocess_of",
                "created_at", "finished_at"],
    record_ref_filters=("subject_model", "subject_id"),
    sortable=["created_at", "updated_at", "finished_at", "status"],
    aggregatable=["id"], groupable=["status", "outcome", "version__workflow", "version__workflow__name"],
    insert=False, update=False, delete=False,
)
_STEP_RESOURCE = hasura_model_resource(
    StepRunType, model=StepRun,
    filterable=["id", "run", "decision_group", "node_key", "map_index", "status", "waiting_kind", "outcome"],
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


@strawberry.type
class WorkflowActionMutation:
    """Operator requests whose authorization and transitions belong to managers."""

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
    _WORKFLOW_RESOURCE, _VERSION_RESOURCE, _RUN_RESOURCE, _STEP_RESOURCE, _ATTEMPT_RESOURCE, _ARTIFACT_RESOURCE,
)
schemas = {
    "console": {
        "query": [resource.query for resource in _RESOURCES],
        "mutation": [WorkflowActionMutation],
        "type_extensions": [DecisionGroupWorkflowExtension],
        "types": [
            RunOrigin,
            WorkflowType, WorkflowVersionType, WorkflowRunType, StepRunType, StepAttemptType, StepArtifactType,
            *(type_ for resource in _RESOURCES for type_ in resource.types),
        ],
    },
}
"""Console-only execution reads and operator mutations composed by the schema owner."""
