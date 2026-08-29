"""GraphQL resources and authored actions for operational work."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_public_id
from angee.projects.schema import DroppedReason, TaskType
from angee.spaces.schema import SpaceGroupType

Queue = apps.get_model("work", "Queue")
Stage = apps.get_model("work", "Stage")
Cycle = apps.get_model("work", "Cycle")
Task = apps.get_model("projects", "Task")


@strawberry_django.type(Queue)
class WorkQueueType(AngeeNode):
    """GraphQL projection of one operational queue."""

    name: auto
    slug: auto
    description: auto
    visibility: auto
    key: auto
    triage_enabled: auto
    cycles_enabled: auto
    cycle_weeks: auto
    cycle_cooldown_weeks: auto
    cycle_start_day: auto
    upcoming_cycle_count: auto
    estimate_scale: auto
    estimate_allow_zero: auto
    default_estimate: auto
    auto_archive_months: auto
    auto_close_months: auto
    created_at: auto
    updated_at: auto

    parent: SpaceGroupType | None = actor_scoped_to_one("parent")
    default_stage: "WorkStageType | None" = actor_scoped_to_one("default_stage")


@strawberry_django.type(Stage)
class WorkStageType(AngeeNode):
    """GraphQL projection of an ordered queue stage."""

    name: auto
    tone: auto
    position: auto
    category: auto
    created_at: auto
    updated_at: auto

    queue: WorkQueueType | None = actor_scoped_to_one("queue")


@strawberry_django.type(Cycle)
class WorkCycleType(AngeeNode):
    """GraphQL projection of one generated queue cycle."""

    number: auto
    starts_on: auto
    ends_on: auto
    completed_at: auto
    uncompleted_upon_close: auto
    created_at: auto
    updated_at: auto

    queue: WorkQueueType | None = actor_scoped_to_one("queue")

    @strawberry_django.field(only=["name", "number"])
    def name(self) -> str:
        """Return a custom name or the model-owned ``Cycle N`` fallback."""

        return cast(Any, self).display_name


@strawberry_django.type(Task, name="TaskType", extend=True)
class TaskWorkExtension:
    """Contribute work columns onto projects' existing task node."""

    number: auto
    estimate: auto
    snoozed_until: auto
    started_triage_at: auto
    triaged_at: auto

    queue: WorkQueueType | None = actor_scoped_to_one("queue")
    stage: WorkStageType | None = actor_scoped_to_one("stage")
    cycle: WorkCycleType | None = actor_scoped_to_one("cycle")

    @strawberry_django.field(only=["snoozed_by_id"])
    def snoozed_by(self) -> strawberry.ID | None:
        """Return the snoozing user's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).snoozed_by_id))

    @strawberry.field
    def work_key(self) -> str | None:
        """Return the queue-keyed task number, such as ``ENG-42``."""

        return cast(Any, self).work_key


@strawberry_django.type(Task, name="ConsoleTaskType", extend=True)
class ConsoleTaskWorkExtension:
    """Contribute work columns onto the console task node."""

    number: auto
    estimate: auto
    snoozed_until: auto
    started_triage_at: auto
    triaged_at: auto

    queue: WorkQueueType | None = actor_scoped_to_one("queue")
    stage: WorkStageType | None = actor_scoped_to_one("stage")
    cycle: WorkCycleType | None = actor_scoped_to_one("cycle")

    @strawberry_django.field(only=["snoozed_by_id"])
    def snoozed_by(self) -> strawberry.ID | None:
        """Return the snoozing user's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).snoozed_by_id))

    @strawberry.field
    def work_key(self) -> str | None:
        """Return the queue-keyed task number, such as ``ENG-42``."""

        return cast(Any, self).work_key


@strawberry.type
class WorkActionMutation:
    """Row-authorized task triage and cycle lifecycle actions."""

    @strawberry.mutation
    @action_guard("Accept task failed.")
    def accept_task(
        self,
        info: strawberry.Info,
        task: PublicID,
        stage: PublicID | None = None,
    ) -> ActionResult:
        """Accept one writable triage task into its selected/default stage."""

        target = authorized_action_target(info, Task, task, "write")
        target_stage = (
            None if stage is None else authorized_action_target(info, Stage, stage, "read")
        )
        target.accept(target_stage)
        return ActionResult(ok=True, message="Task accepted.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Decline task failed.")
    def decline_task(
        self,
        info: strawberry.Info,
        task: PublicID,
        reason: DroppedReason,  # type: ignore[valid-type]
    ) -> ActionResult:
        """Decline one writable triage task for a closed reason."""

        target = authorized_action_target(info, Task, task, "write")
        target.decline(reason)
        return ActionResult(ok=True, message="Task declined.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Snooze task failed.")
    def snooze_task(
        self,
        info: strawberry.Info,
        task: PublicID,
        until: datetime,
    ) -> ActionResult:
        """Snooze one writable triage task until time or chatter wakes it."""

        target = authorized_action_target(info, Task, task, "write")
        target.snooze(until)
        return ActionResult(ok=True, message="Task snoozed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Mark duplicate failed.")
    def mark_task_duplicate(
        self,
        info: strawberry.Info,
        task: PublicID,
        canonical: PublicID,
    ) -> ActionResult:
        """Merge one writable task into a writable canonical task."""

        target = authorized_action_target(info, Task, task, "write")
        canonical_target = authorized_action_target(info, Task, canonical, "write")
        target.mark_duplicate(canonical_target)
        return ActionResult(ok=True, message="Task marked duplicate.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Close cycle failed.")
    def close_work_cycle(self, info: strawberry.Info, cycle: PublicID) -> ActionResult:
        """Close and roll over one writable generated cycle."""

        target = authorized_action_target(info, Cycle, cycle, "write")
        target.close()
        return ActionResult(ok=True, message="Cycle closed.", id=target.sqid)


_QUEUE_RESOURCE = hasura_model_resource(
    WorkQueueType,
    model=Queue,
    name="work_queues",
    filterable=[
        "id",
        "name",
        "slug",
        "key",
        "visibility",
        "parent",
        "triage_enabled",
        "cycles_enabled",
        "estimate_scale",
        "default_stage",
        "created_at",
        "updated_at",
    ],
    sortable=["key", "name", "slug", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=[
        "visibility",
        "parent",
        "triage_enabled",
        "cycles_enabled",
        "estimate_scale",
        "default_stage",
    ],
    insertable=[
        "name",
        "slug",
        "description",
        "visibility",
        "parent",
        "key",
        "triage_enabled",
        "cycles_enabled",
        "cycle_weeks",
        "cycle_cooldown_weeks",
        "cycle_start_day",
        "upcoming_cycle_count",
        "estimate_scale",
        "estimate_allow_zero",
        "default_estimate",
        "auto_archive_months",
        "auto_close_months",
    ],
    updatable=[
        "name",
        "slug",
        "description",
        "visibility",
        "parent",
        "key",
        "triage_enabled",
        "cycles_enabled",
        "cycle_weeks",
        "cycle_cooldown_weeks",
        "cycle_start_day",
        "upcoming_cycle_count",
        "estimate_scale",
        "estimate_allow_zero",
        "default_estimate",
        "default_stage",
        "auto_archive_months",
        "auto_close_months",
    ],
    field_id_decode={
        "parent": public_pk_decoder(apps.get_model("spaces", "Group")),
        "default_stage": public_pk_decoder(Stage),
    },
    write_backend=AngeeHasuraWriteBackend(
        Queue,
        public_id_fields=("parent", "default_stage"),
    ),
)

_STAGE_RESOURCE = hasura_model_resource(
    WorkStageType,
    model=Stage,
    name="work_stages",
    filterable=[
        "id",
        "queue",
        "name",
        "tone",
        "position",
        "category",
        "created_at",
        "updated_at",
    ],
    sortable=["queue", "position", "name", "created_at", "updated_at"],
    aggregatable=["id", "position"],
    groupable=["queue", "tone", "category"],
    insertable=["queue", "name", "tone", "position", "category"],
    updatable=["name", "tone", "position", "category"],
    field_id_decode={"queue": public_pk_decoder(Queue)},
    write_backend=AngeeHasuraWriteBackend(Stage, public_id_fields=("queue",)),
)

_CYCLE_RESOURCE = hasura_model_resource(
    WorkCycleType,
    model=Cycle,
    name="work_cycles",
    filterable=[
        "id",
        "queue",
        "number",
        "name",
        "starts_on",
        "ends_on",
        "completed_at",
        "created_at",
        "updated_at",
    ],
    sortable=[
        "queue",
        "number",
        "name",
        "starts_on",
        "ends_on",
        "completed_at",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id", "number"],
    groupable=["queue", "starts_on", "ends_on", "completed_at"],
    insert=False,
    updatable=["name"],
    delete=False,
    write_backend=AngeeHasuraWriteBackend(Cycle),
)

_RESOURCE_TYPES = [*_QUEUE_RESOURCE.types, *_STAGE_RESOURCE.types, *_CYCLE_RESOURCE.types]

_WORK_SCHEMA_BUCKET = {
    "query": [_QUEUE_RESOURCE.query, _STAGE_RESOURCE.query, _CYCLE_RESOURCE.query],
    "mutation": [
        WorkActionMutation,
        _QUEUE_RESOURCE.mutation,
        _STAGE_RESOURCE.mutation,
        _CYCLE_RESOURCE.mutation,
    ],
    "types": [WorkQueueType, WorkStageType, WorkCycleType, TaskType, *_RESOURCE_TYPES],
    "type_extensions": [TaskWorkExtension],
}

schemas = {
    "public": {**_WORK_SCHEMA_BUCKET},
    "console": {
        **_WORK_SCHEMA_BUCKET,
        "type_extensions": [
            *_WORK_SCHEMA_BUCKET["type_extensions"],
            ConsoleTaskWorkExtension,
        ],
        "subscription": [
            changes(Queue, field="workQueueChanged"),
            changes(Stage, field="workStageChanged"),
            changes(Cycle, field="workCycleChanged"),
        ],
    },
}
