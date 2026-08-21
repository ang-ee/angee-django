"""Strawberry-Django resources and authored actions for projects."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data import (
    AngeeHasuraWriteBackend,
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.ids import PublicID, optional_public_id, require_public_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
from django.apps import apps
from django.contrib.auth import get_user_model
from strawberry import auto
from strawberry.scalars import JSON

from angee.iam.audit import AuthoredRefMixin
from angee.iam.identity import user_public_id
from angee.parties.schema import PartyType
from angee.storage.schema import FolderType

Project = apps.get_model("projects", "Project")
Milestone = apps.get_model("projects", "Milestone")
Task = apps.get_model("projects", "Task")
TaskRelation = apps.get_model("projects", "TaskRelation")
Participant = apps.get_model("projects", "Participant")
Link = apps.get_model("projects", "Link")
ThreadActivity = apps.get_model("messaging", "ThreadActivity")
Folder = apps.get_model("storage", "Folder")
Party = apps.get_model("parties", "Party")
User = get_user_model()

_TASK_EXTENSION_READ_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_readable_fields",
)
_TASK_EXTENSION_FILTER_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_filterable_fields",
)
_TASK_EXTENSION_ORDER_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_sortable_fields",
)
_TASK_EXTENSION_AGGREGATE_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_aggregatable_fields",
)
_TASK_EXTENSION_GROUP_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_groupable_fields",
)
_TASK_EXTENSION_INSERT_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_insertable_fields",
)
_TASK_EXTENSION_UPDATE_FIELDS = declared_hasura_resource_fields(
    Task,
    "hasura_updatable_fields",
)
_TASK_EXTENSION_FORBIDDEN_INSERT_FIELDS = set(
    declared_hasura_resource_fields(Task, "hasura_forbidden_insertable_fields")
)
_TASK_EXTENSION_WRITE_FIELDS = tuple(
    dict.fromkeys((*_TASK_EXTENSION_INSERT_FIELDS, *_TASK_EXTENSION_UPDATE_FIELDS))
)
_TASK_EXTENSION_PUBLIC_ID_FIELDS = tuple(
    name
    for name in _TASK_EXTENSION_WRITE_FIELDS
    if Task._meta.get_field(name).is_relation
)

DroppedReason = Task._meta.get_field("dropped_reason").choices_enum
strawberry.enum(cast(Any, DroppedReason))


@strawberry.input
class ProjectLinkTargetInput:
    """A project or task record that may own an external link."""

    model_label: str = strawberry.field(name="model_label")
    record_id: PublicID = strawberry.field(name="record_id")


@strawberry_django.type(Project)
class ProjectType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a bounded project."""

    title: auto
    body: auto
    status: auto
    start_date: auto
    start_date_resolution: auto
    target_date: auto
    target_date_resolution: auto
    created_at: auto
    updated_at: auto

    folder: FolderType | None = actor_scoped_to_one("folder")
    converted_from: "TaskType | None" = actor_scoped_to_one("converted_from")

    @strawberry_django.field(only=["lead_id"])
    def lead(self) -> strawberry.ID | None:
        """Return the lead's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).lead_id))


@strawberry_django.type(Milestone)
class MilestoneType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a project milestone."""

    name: auto
    description: auto
    target_date: auto
    sort_order: auto
    created_at: auto
    updated_at: auto

    project: ProjectType | None = actor_scoped_to_one("project")


@strawberry_django.type(Task)
class TaskType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of one human action."""

    title: auto
    note: auto
    status: auto
    dropped_reason: auto
    priority: auto
    due_date: auto
    recurrence: auto
    sort_order: auto
    sub_sort_order: auto
    done_at: auto
    dropped_at: auto
    created_at: auto
    updated_at: auto

    project: ProjectType | None = actor_scoped_to_one("project")
    milestone: MilestoneType | None = actor_scoped_to_one("milestone")
    parent: "TaskType | None" = actor_scoped_to_one("parent")

    @strawberry_django.field(only=["assignee_id"])
    def assignee(self) -> strawberry.ID | None:
        """Return the assignee's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).assignee_id))

    @strawberry_django.field(only=["delegate_id"])
    def delegate(self) -> strawberry.ID | None:
        """Return the delegated agent user's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).delegate_id))

    @strawberry_django.field(only=["converted_from_activity_id"])
    def converted_from_activity(self) -> strawberry.ID | None:
        """Return the provenance activity's public id without exposing its row."""

        activity_id = cast(Any, self).converted_from_activity_id
        return None if activity_id is None else require_public_id(ThreadActivity, activity_id)


@strawberry_django.type(TaskRelation)
class TaskRelationType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a canonical directed task relation."""

    kind: auto
    created_at: auto
    updated_at: auto

    task: TaskType | None = actor_scoped_to_one("task")
    related_task: TaskType | None = actor_scoped_to_one("related_task")

    @strawberry.field
    def inverse_kind(self) -> str:
        """Return the computed relation vocabulary from the other endpoint."""

        return cast(Any, self).inverse_kind


@strawberry_django.type(Participant)
class ProjectParticipantType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of project involvement, not access."""

    kind: auto
    created_at: auto
    updated_at: auto

    project: ProjectType | None = actor_scoped_to_one("project")
    party: PartyType | None = actor_scoped_to_one("party")


@strawberry_django.type(Link)
class ProjectLinkType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a project/task external URL."""

    url: auto
    title: auto
    metadata: JSON
    created_at: auto
    updated_at: auto

    @strawberry.field
    def target_model(self) -> str:
        """Return the target's Django model label."""

        return cast(Any, self).record_model_label

    @strawberry.field
    def target_id(self) -> strawberry.ID:
        """Return the target's stable public id."""

        return cast(strawberry.ID, cast(Any, self).record_public_id)


@strawberry.type
class ProjectTaskActionMutation:
    """Row-authorized lifecycle and maturation actions."""

    @strawberry.mutation
    @action_guard("Complete task failed.")
    def complete_task(self, info: strawberry.Info, task: PublicID) -> ActionResult:
        """Complete one writable task."""

        target = authorized_action_target(info, Task, task, "write")
        target.complete()
        return ActionResult(ok=True, message="Task completed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Drop task failed.")
    def drop_task(
        self,
        info: strawberry.Info,
        task: PublicID,
        reason: DroppedReason,  # type: ignore[valid-type]
    ) -> ActionResult:
        """Drop one writable task for a closed reason."""

        target = authorized_action_target(info, Task, task, "write")
        target.drop(reason)
        return ActionResult(ok=True, message="Task dropped.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Reopen task failed.")
    def reopen_task(self, info: strawberry.Info, task: PublicID) -> ActionResult:
        """Reopen one writable task."""

        target = authorized_action_target(info, Task, task, "write")
        target.reopen()
        return ActionResult(ok=True, message="Task reopened.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Promote task to project failed.")
    def promote_task_to_project(self, info: strawberry.Info, task: PublicID) -> ActionResult:
        """Promote one writable task while retaining its identity and provenance."""

        target = authorized_action_target(info, Task, task, "write")
        project = target.promote_to_project()
        return ActionResult(ok=True, message="Task promoted to project.", id=project.sqid)

    @strawberry.mutation
    @action_guard("Promote activity to task failed.")
    def promote_thread_activity_to_task(
        self,
        info: strawberry.Info,
        activity: PublicID,
    ) -> ActionResult:
        """Promote one writable chatter activity to a task with provenance."""

        target = authorized_action_target(info, ThreadActivity, activity, "write")
        task = target.promote_to_task()
        return ActionResult(ok=True, message="Activity promoted to task.", id=task.sqid)

    @strawberry.mutation
    @action_guard("Attach link failed.")
    def attach_link(
        self,
        info: strawberry.Info,
        target: ProjectLinkTargetInput,
        url: str,
        title: str = "",
        metadata: JSON | None = None,
    ) -> ActionResult:
        """Create or refresh one URL-keyed link on a writable project or task."""

        target_model = Link.objects.target_model(target.model_label)
        target_record = authorized_action_target(info, target_model, target.record_id, "write")
        link = Link.objects.upsert(
            target=target_record,
            url=url,
            title=title,
            metadata=cast(dict[str, Any] | None, metadata),
        )
        return ActionResult(ok=True, message="Link attached.", id=link.sqid)


_PROJECT_RESOURCE = hasura_model_resource(
    ProjectType,
    model=Project,
    name="projects",
    filterable=[
        "id",
        "title",
        "status",
        "lead",
        "start_date",
        "start_date_resolution",
        "target_date",
        "target_date_resolution",
        "folder",
        "converted_from",
        "created_at",
        "updated_at",
    ],
    sortable=["title", "status", "start_date", "target_date", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=[
        "status",
        "lead",
        "start_date_resolution",
        "target_date_resolution",
        "folder",
        "target_date",
    ],
    insertable=[
        "title",
        "body",
        "status",
        "lead",
        "start_date",
        "start_date_resolution",
        "target_date",
        "target_date_resolution",
        "folder",
    ],
    updatable=[
        "title",
        "body",
        "lead",
        "start_date",
        "start_date_resolution",
        "target_date",
        "target_date_resolution",
        "folder",
    ],
    field_id_decode={
        "lead": public_pk_decoder(User),
        "folder": public_pk_decoder(Folder),
        "converted_from": public_pk_decoder(Task),
    },
    write_backend=AngeeHasuraWriteBackend(Project, public_id_fields=("lead", "folder")),
)

_MILESTONE_RESOURCE = hasura_model_resource(
    MilestoneType,
    model=Milestone,
    name="project_milestones",
    filterable=["id", "project", "name", "target_date", "created_at", "updated_at"],
    sortable=["project", "sort_order", "name", "target_date", "created_at", "updated_at"],
    aggregatable=["id", "sort_order"],
    groupable=["project", "target_date"],
    insertable=["project", "name", "description", "target_date", "sort_order"],
    updatable=["project", "name", "description", "target_date", "sort_order"],
    field_id_decode={"project": public_pk_decoder(Project)},
    write_backend=AngeeHasuraWriteBackend(Milestone, public_id_fields=("project",)),
)

_TASK_RESOURCE = hasura_model_resource(
    TaskType,
    model=Task,
    name="project_tasks",
    filterable=[
        "id",
        "project",
        "milestone",
        "parent",
        "title",
        "status",
        "dropped_reason",
        "assignee",
        "delegate",
        "priority",
        "due_date",
        "recurrence",
        "done_at",
        "dropped_at",
        "converted_from_activity",
        "created_at",
        "updated_at",
        *_TASK_EXTENSION_FILTER_FIELDS,
    ],
    sortable=[
        "project",
        "milestone",
        "parent",
        "sort_order",
        "sub_sort_order",
        "title",
        "status",
        "priority",
        "due_date",
        "done_at",
        "dropped_at",
        "created_at",
        "updated_at",
        *_TASK_EXTENSION_ORDER_FIELDS,
    ],
    aggregatable=[
        "id",
        "sort_order",
        "sub_sort_order",
        *_TASK_EXTENSION_AGGREGATE_FIELDS,
    ],
    groupable=[
        "project",
        "milestone",
        "parent",
        "status",
        "dropped_reason",
        "assignee",
        "delegate",
        "priority",
        "due_date",
        *_TASK_EXTENSION_GROUP_FIELDS,
    ],
    insertable=[
        *(
            field
            for field in (
                "project",
                "milestone",
                "parent",
                "title",
                "note",
                "status",
                "dropped_reason",
                "assignee",
                "delegate",
                "priority",
                "due_date",
                "recurrence",
                "sort_order",
                "sub_sort_order",
            )
            if field not in _TASK_EXTENSION_FORBIDDEN_INSERT_FIELDS
        ),
        *_TASK_EXTENSION_INSERT_FIELDS,
    ],
    updatable=[
        "project",
        "milestone",
        "parent",
        "title",
        "note",
        "assignee",
        "delegate",
        "priority",
        "due_date",
        "recurrence",
        "sort_order",
        "sub_sort_order",
        *_TASK_EXTENSION_UPDATE_FIELDS,
    ],
    field_id_decode={
        "project": public_pk_decoder(Project),
        "milestone": public_pk_decoder(Milestone),
        "parent": public_pk_decoder(Task),
        "assignee": public_pk_decoder(User),
        "delegate": public_pk_decoder(User),
        "converted_from_activity": public_pk_decoder(ThreadActivity),
        **{
            name: public_pk_decoder(Task._meta.get_field(name).related_model)
            for name in _TASK_EXTENSION_PUBLIC_ID_FIELDS
        },
    },
    write_backend=AngeeHasuraWriteBackend(
        Task,
        public_id_fields=(
            "project",
            "milestone",
            "parent",
            "assignee",
            "delegate",
            *_TASK_EXTENSION_PUBLIC_ID_FIELDS,
        ),
    ),
    declared_fields=_TASK_EXTENSION_READ_FIELDS,
)

_TASK_RELATION_RESOURCE = hasura_model_resource(
    TaskRelationType,
    model=TaskRelation,
    name="project_task_relations",
    filterable=["id", "task", "related_task", "kind", "created_at", "updated_at"],
    sortable=["task", "related_task", "kind", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["task", "related_task", "kind"],
    insertable=["task", "related_task", "kind"],
    updatable=["kind"],
    field_id_decode={
        "task": public_pk_decoder(Task),
        "related_task": public_pk_decoder(Task),
    },
    write_backend=AngeeHasuraWriteBackend(TaskRelation, public_id_fields=("task", "related_task")),
)

_PARTICIPANT_RESOURCE = hasura_model_resource(
    ProjectParticipantType,
    model=Participant,
    name="project_participants",
    filterable=["id", "project", "party", "kind", "created_at", "updated_at"],
    sortable=["project", "party", "kind", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["project", "party", "kind"],
    insertable=["project", "party", "kind"],
    updatable=["kind"],
    field_id_decode={
        "project": public_pk_decoder(Project),
        "party": public_pk_decoder(Party),
    },
    write_backend=AngeeHasuraWriteBackend(Participant, public_id_fields=("project", "party")),
)

_LINK_RESOURCE = hasura_model_resource(
    ProjectLinkType,
    model=Link,
    name="project_links",
    filterable=["id", "url", "title", "created_at", "updated_at"],
    sortable=["url", "title", "created_at", "updated_at"],
    aggregatable=["id"],
    groupable=["url"],
    insert=False,
    updatable=["title", "metadata"],
    write_backend=AngeeHasuraWriteBackend(Link),
)

_RESOURCE_TYPES = [
    *_PROJECT_RESOURCE.types,
    *_MILESTONE_RESOURCE.types,
    *_TASK_RESOURCE.types,
    *_TASK_RELATION_RESOURCE.types,
    *_PARTICIPANT_RESOURCE.types,
    *_LINK_RESOURCE.types,
]

_PROJECTS_SCHEMA_BUCKET = {
    "query": [
        _PROJECT_RESOURCE.query,
        _MILESTONE_RESOURCE.query,
        _TASK_RESOURCE.query,
        _TASK_RELATION_RESOURCE.query,
        _PARTICIPANT_RESOURCE.query,
        _LINK_RESOURCE.query,
        revisions(ProjectType),
    ],
    "mutation": [
        ProjectTaskActionMutation,
        _PROJECT_RESOURCE.mutation,
        _MILESTONE_RESOURCE.mutation,
        _TASK_RESOURCE.mutation,
        _TASK_RELATION_RESOURCE.mutation,
        _PARTICIPANT_RESOURCE.mutation,
        _LINK_RESOURCE.mutation,
    ],
    "types": [
        ProjectType,
        MilestoneType,
        TaskType,
        TaskRelationType,
        ProjectParticipantType,
        ProjectLinkType,
        *_RESOURCE_TYPES,
    ],
}

schemas = {
    "public": {**_PROJECTS_SCHEMA_BUCKET},
    "console": {
        **_PROJECTS_SCHEMA_BUCKET,
        "subscription": [
            changes(Project, field="projectChanged"),
            changes(Milestone, field="projectMilestoneChanged"),
            changes(Task, field="projectTaskChanged"),
            changes(TaskRelation, field="projectTaskRelationChanged"),
            changes(Participant, field="projectParticipantChanged"),
            changes(Link, field="projectLinkChanged"),
        ],
    },
}
