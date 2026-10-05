"""GraphQL resources and authored actions for operational work."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import F
from rebac import system_context, to_subject_ref
from strawberry import auto

from angee.base.identity import public_subject_ref
from angee.graphql.actions import (
    ActionResult,
    ActionSelectionInput,
    action_guard,
    authorized_action_target,
    authorized_permission_target,
    many_actions,
)
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.inputs import InputReference
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_label, user_public_id
from angee.projects.schema import DroppedReason, TaskType
from angee.spaces.schema import SpaceGroupType

Queue = apps.get_model("work", "Queue")
Stage = apps.get_model("work", "Stage")
Cycle = apps.get_model("work", "Cycle")
Task = apps.get_model("projects", "Task")
Project = apps.get_model("projects", "Project")
User = apps.get_model("iam", "User")
Membership = apps.get_model("spaces", "Membership")
Person = apps.get_model("parties", "Person")
Milestone = apps.get_model("projects", "Milestone")


@strawberry_django.type(Queue)
class WorkQueueType(AngeeNode):
    """GraphQL projection of one operational queue."""

    name: auto
    slug: auto
    description: auto
    visibility: auto
    key: auto
    triage_enabled: auto
    provision_stages: auto
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
    permissions = permissions_field(("read", "write", "share"))

    parent: SpaceGroupType | None = actor_scoped_to_one("parent")
    default_stage: "WorkStageType | None" = actor_scoped_to_one("default_stage")


@strawberry_django.type(Stage)
class WorkStageType(AngeeNode):
    """GraphQL projection of an ordered queue stage."""

    name: auto
    tone: auto
    position: auto
    category: auto
    rule_owned: auto
    conceals: auto
    on_path: bool = strawberry_django.field(only=["conceals", "category"])
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


@strawberry_django.type(Project, name="ProjectType", extend=True)
class ProjectWorkExtension:
    """Expose the optional team through the shared relation redaction owner."""

    team: SpaceGroupType | None = actor_scoped_to_one("team")


@strawberry_django.type(Project, name="ConsoleProjectType", extend=True)
class ConsoleProjectWorkExtension:
    """Contribute the optional team to the console project node."""

    team: SpaceGroupType | None = actor_scoped_to_one("team")


@strawberry_django.type(Milestone, name="MilestoneType", extend=True)
class MilestoneWorkExtension:
    """Expose the phase's rule stage through the shared relation redaction owner."""

    active_stage: WorkStageType | None = actor_scoped_to_one("active_stage")


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

    @strawberry_django.field(annotate={"_stage_name": F("stage__name")})
    def stage_name(self) -> str | None:
        """Expose a readable task's status label without granting its queue."""

        return cast(Any, self)._stage_name

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

    stage_name = TaskWorkExtension.__dict__["stage_name"]

    @strawberry_django.field(only=["snoozed_by_id"])
    def snoozed_by(self) -> strawberry.ID | None:
        """Return the snoozing user's public id without exposing auth/user."""

        return optional_public_id(user_public_id(cast(Any, self).snoozed_by_id))

    @strawberry.field
    def work_key(self) -> str | None:
        """Return the queue-keyed task number, such as ``ENG-42``."""

        return cast(Any, self).work_key


@strawberry.input(name="ProjectSetupInput", extend=True)
class ProjectWorkSetupInput:
    """Work contributes the project team without coupling projects to work."""

    team: PublicID = strawberry.field(metadata={InputReference: InputReference("spaces.Group")})


@strawberry.input(name="ProjectMilestoneSetupInput", extend=True)
class MilestoneWorkSetupInput:
    """Work contributes the stage mapping to the typed milestone template."""

    active_stage: PublicID | None = strawberry.field(
        default=None, metadata={InputReference: InputReference("work.Stage")},
    )


@action_guard("Accept task failed.")
def accept_task(
    info: strawberry.Info,
    task: PublicID,
    stage: PublicID | None = None,
    expected_revision: int | None = None,
) -> ActionResult:
    """Accept one writable triage task into its selected/default stage."""

    target = authorized_action_target(info, Task, task, "write")
    target_stage = None if stage is None else authorized_permission_target(info, Stage, stage, "read")
    target.accept(target_stage, expected_revision=expected_revision)
    return ActionResult(ok=True, message="Task accepted.", id=target.sqid)

@action_guard("Decline task failed.")
def decline_task(
    info: strawberry.Info,
    task: PublicID,
    reason: DroppedReason,
    expected_revision: int | None = None,
) -> ActionResult:
    """Decline one writable triage task for a closed reason."""

    target = authorized_action_target(info, Task, task, "write")
    target.decline(reason, expected_revision=expected_revision)
    return ActionResult(ok=True, message="Task declined.", id=target.sqid)

@action_guard("Remove task failed.")
def remove_task(info: strawberry.Info, task: PublicID, expected_revision: int, reason: str = "") -> ActionResult:
    """Conceal a writable task through its stage owner, recording an optional reason."""

    target = authorized_permission_target(info, Task, task, "write")
    target.remove(reason=reason, expected_revision=expected_revision)
    return ActionResult(ok=True, message="Task removed.", id=target.sqid)


@action_guard("Restore task failed.")
def restore_task(info: strawberry.Info, task: PublicID, expected_revision: int) -> ActionResult:
    """Return a removed task to the stage it held before removal."""

    target = authorized_permission_target(info, Task, task, "write")
    target.restore(expected_revision=expected_revision)
    return ActionResult(ok=True, message="Task restored.", id=target.sqid)


@strawberry.type
class ProjectManagerPersonType:
    """One account holding the project's team moderator seat."""

    subject: str
    label: str
    seat_id: PublicID
    removable: bool


@strawberry.type
class ProjectManagerRosterType:
    """The manager seats and admission offer under the same roster policy."""

    offered: bool
    people: list[ProjectManagerPersonType]


@strawberry.type
class WorkPeopleQuery:
    """Project team managers, derived from the live spaces roster."""

    @strawberry.field
    def project_manager_roster(self, info: strawberry.Info, project: PublicID) -> ProjectManagerRosterType:
        """List only person accounts on the project's current team."""

        target = authorized_permission_target(info, Project, project, "read")
        if target.team_id is None:
            return ProjectManagerRosterType(offered=False, people=[])
        offered = target.has_access("share") and (
            Membership.MembershipRole.MODERATOR in Membership.objects.available_roles(group=target.team)
        )
        with system_context(reason="work.project.manager_roster"):
            seats = list(Membership._base_manager.filter(
                group_id=target.team_id, role=Membership.MembershipRole.MODERATOR,
                is_confirmed=True, is_dismissed=False,
            ).order_by("pk")[:100])
            people = {
                person.pk: person.user for person in Person._base_manager.filter(
                    pk__in=[seat.party_id for seat in seats], user__kind="person",
                ).select_related("user")
            }
        return ProjectManagerRosterType(offered=offered, people=[
            ProjectManagerPersonType(
                subject=str(public_subject_ref(to_subject_ref(people[seat.party_id]))),
                label=user_label(people[seat.party_id]), seat_id=seat.sqid, removable=offered,
            )
            for seat in seats if seat.party_id in people
        ])


@strawberry.type
class WorkActionMutation:
    """Row-authorized task triage and cycle lifecycle actions."""

    @strawberry.mutation
    @action_guard("Project manager admission failed.")
    def admit_project_manager(self, info: strawberry.Info, project: PublicID, user: PublicID) -> ActionResult:
        """Use the team's roster verb and follow the project atomically."""

        target = authorized_permission_target(info, Project, project, "share")
        account = authorized_permission_target(info, User, user, "read")
        target.admit_manager(account)
        return ActionResult(ok=True, message="Project manager admitted.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Project manager removal failed.")
    def remove_project_manager(self, info: strawberry.Info, project: PublicID, seat: PublicID) -> ActionResult:
        """Dismiss the selected moderator seat on this project's team."""

        target = authorized_permission_target(info, Project, project, "share")
        membership = authorized_action_target(info, Membership, seat, "write")
        target.remove_manager(membership)
        return ActionResult(ok=True, message="Project manager removed.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Start task failed.")
    def start_task(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Start one writable queued task."""

        target = authorized_action_target(info, Task, id, "write")
        target.start()
        return ActionResult(ok=True, message="Task started.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Return task to triage failed.")
    def return_task_to_triage(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Return one writable task to its queue's triage stage."""

        target = authorized_action_target(info, Task, id, "write")
        target.return_to_triage()
        return ActionResult(ok=True, message="Task returned to triage.", id=target.sqid)

    accept_task = strawberry.mutation(resolver=accept_task)

    decline_task = strawberry.mutation(resolver=decline_task)

    remove_task = strawberry.mutation(resolver=remove_task)

    restore_task = strawberry.mutation(resolver=restore_task)

    @strawberry.mutation
    def accept_tasks(self, info: strawberry.Info, selection: list[ActionSelectionInput]) -> list[ActionResult]:
        """Accept selected tasks; refusals do not roll back eligible rows."""

        return many_actions(selection, lambda item: accept_task(
            info, item.id, expected_revision=item.expected_revision,
        ))

    @strawberry.mutation
    def decline_tasks(
        self, info: strawberry.Info, selection: list[ActionSelectionInput], reason: DroppedReason,
    ) -> list[ActionResult]:
        """Decline selected tasks; refusals do not roll back eligible rows."""

        return many_actions(selection, lambda item: decline_task(
            info, item.id, reason, expected_revision=item.expected_revision,
        ))

    @strawberry.mutation
    def remove_tasks(
        self, info: strawberry.Info, selection: list[ActionSelectionInput], reason: str = "",
    ) -> list[ActionResult]:
        """Conceal selected tasks; refusals do not roll back eligible rows."""

        return many_actions(selection, lambda item: remove_task(
            info, item.id, item.expected_revision, reason,
        ))

    @strawberry.mutation
    def restore_tasks(self, info: strawberry.Info, selection: list[ActionSelectionInput]) -> list[ActionResult]:
        """Restore selected removed tasks; refusals do not roll back eligible rows."""

        return many_actions(selection, lambda item: restore_task(
            info, item.id, item.expected_revision,
        ))

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
        "provision_stages",
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
    # Queue catalogues are bounded configuration sets. Keep their presentation
    # in the browser so identifier keys use the shared natural text comparator
    # (P2 before P10) instead of database lexicographic ordering.
    row_model="client",
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
        "rule_owned",
        "conceals",
        "created_at",
        "updated_at",
    ],
    sortable=["queue", "position", "name", "created_at", "updated_at"],
    aggregatable=["id", "position"],
    groupable=["queue", "tone", "category"],
    insertable=["queue", "name", "tone", "position", "category", "rule_owned", "conceals"],
    updatable=["name", "tone", "position", "category", "rule_owned", "conceals"],
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

_WORK_SCHEMA_BUCKET: dict[str, list[Any]] = {
    "query": [WorkPeopleQuery, _QUEUE_RESOURCE.query, _STAGE_RESOURCE.query, _CYCLE_RESOURCE.query],
    "mutation": [
        WorkActionMutation,
        _QUEUE_RESOURCE.mutation,
        _STAGE_RESOURCE.mutation,
        _CYCLE_RESOURCE.mutation,
    ],
    "types": [ProjectManagerPersonType, ProjectManagerRosterType,
              WorkQueueType, WorkStageType, WorkCycleType, TaskType, *_RESOURCE_TYPES],
    "input_extensions": [ProjectWorkSetupInput, MilestoneWorkSetupInput],
    "type_extensions": [TaskWorkExtension, ProjectWorkExtension, MilestoneWorkExtension],
}

schemas = {
    "public": {**_WORK_SCHEMA_BUCKET},
    "console": {
        **_WORK_SCHEMA_BUCKET,
        "type_extensions": [
            *_WORK_SCHEMA_BUCKET["type_extensions"],
            ConsoleTaskWorkExtension,
            ConsoleProjectWorkExtension,
        ],
        "subscription": [
            changes(Queue, field="workQueueChanged"),
            changes(Stage, field="workStageChanged"),
            changes(Cycle, field="workCycleChanged"),
        ],
    },
}
