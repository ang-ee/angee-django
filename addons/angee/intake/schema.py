"""GraphQL resource and authored actions for external-request intake."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from strawberry import auto
from strawberry.scalars import JSON

from angee.decisions.schema import DecisionType
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.capabilities import held_permissions, permission_annotations, permissions_field
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID, optional_public_id
from angee.graphql.inputs import InputReference
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_public_id
from angee.iam.schema import UserType
from angee.intake.models import NeedAccessAction, task_requester_name, task_requester_rows
from angee.messaging.schema import ChannelType, MessageType
from angee.parties.schema import PartyType
from angee.projects.schema import ProjectType, TaskType
from angee.work.schema import WorkQueueType

Need = apps.get_model("intake", "Need")
User = apps.get_model("iam", "User")
Channel = apps.get_model("messaging", "Channel")
Party = apps.get_model("parties", "Party")
Project = apps.get_model("projects", "Project")
Task = apps.get_model("projects", "Task")
Message = apps.get_model("messaging", "Message")
Queue = apps.get_model("work", "Queue")

NeedImportance = Need._meta.get_field("importance").choices_enum
strawberry.enum(cast(Any, NeedImportance))
strawberry.enum(cast(Any, NeedAccessAction))


@strawberry.input(name="ProjectSetupInput", extend=True)
class ProjectIntakeSetupInput:
    """Assign the Need's party through the shared setup input."""

    party: PublicID | None = strawberry.field(
        default=None, metadata={InputReference: InputReference("parties.Party")},
    )


@strawberry.input
class NeedTargetInput:
    """A task or project address accepted by manual need capture."""

    model_label: str = strawberry.field(name="model_label")
    record_id: PublicID = strawberry.field(name="record_id")


@strawberry_django.type(Need)
class NeedType(AngeeNode):
    """GraphQL projection of one external-request evidence row."""

    importance: auto
    body: auto
    revision: auto
    permissions = permissions_field(("write",))
    claimed_name: auto
    claimed_email: str | None

    @strawberry_django.field(annotate={
        "_requester_user_id": lambda info: models.Subquery(
            apps.get_model("parties", "Person")._base_manager.filter(
                pk=models.OuterRef("party_id"),
            ).values("user_id")[:1],
        ),
    })
    def requester_user(self) -> strawberry.ID | None:
        """Project the linked account without resolving each party separately."""

        return optional_public_id(user_public_id(cast(Any, self)._requester_user_id))
    requester_access_granted: bool = strawberry_django.field(only=["admitted_user_id"])
    access_decision: DecisionType | None = actor_scoped_to_one("access_decision")
    access_verdict: JSON | None = strawberry_django.field(
        only=["access_decision_id"], prefetch_related=["access_decision"],
    )
    access_answered_at: datetime | None = strawberry_django.field(
        only=["access_decision_id"], prefetch_related=["access_decision"],
    )
    created_at: auto
    updated_at: auto

    party: PartyType | None = actor_scoped_to_one("party")
    task: TaskType | None = actor_scoped_to_one("task")
    project: ProjectType | None = actor_scoped_to_one("project")
    source_message: MessageType | None = actor_scoped_to_one("source_message")
    original_task: TaskType | None = actor_scoped_to_one("original_task")
    access_answered_by: UserType | None = strawberry_django.field(
        only=["access_decision_id"], prefetch_related=["access_decision__answered_by"],
    )


@strawberry.type
class TaskRequester:
    """Name visible with a task; contact detail visible only to its writers."""

    display_name: str
    email: str | None


def _task_requester(root: Any) -> TaskRequester | None:
    name = cast(str | None, root._intake_requester_name)
    if not name:
        return None
    email = cast(str | None, root._intake_requester_email)
    return TaskRequester(
        display_name=name,
        email=email or None if "write" in held_permissions(root, ("write",)) else None,
    )


@strawberry.type
class TaskIntakeFields:
    """Shared intake attribution for task collection projections."""

    requester: TaskRequester | None = strawberry_django.field(
        resolver=_task_requester,
        annotate={
            # The same first request the task's requester filters read (intake.models).
            "_intake_requester_name": lambda info: task_requester_name(),
            "_intake_requester_email": lambda info: models.Subquery(
                task_requester_rows().values("claimed_email")[:1],
                output_field=models.TextField(),
            ),
            "_angee_permission_actor": lambda info: permission_annotations(Task, ())["_angee_permission_actor"],
            "_angee_permission_write": lambda info: permission_annotations(Task, ("write",))["_angee_permission_write"],
        },
    )


@strawberry_django.type(Task, name="TaskType", extend=True)
class TaskIntakeExtension(TaskIntakeFields):
    """Public task contribution from intake."""


@strawberry_django.type(Task, name="ConsoleTaskType", extend=True)
class ConsoleTaskIntakeExtension(TaskIntakeFields):
    """Console task contribution from intake."""


@strawberry_django.type(Channel, name="ChannelType", extend=True)
class ChannelIntakeExtension:
    """Contribute intake configuration onto messaging's channel node."""

    intake_trigger: auto
    intake_field_map: auto
    intake_requester_domains: auto
    intake_queue: WorkQueueType | None = actor_scoped_to_one("intake_queue")


@strawberry.type
class IntakeActionMutation:
    """Row-authorized manual capture and Need-to-Task conversion actions."""

    @strawberry.mutation
    @action_guard("File task with need failed.")
    def file_task_with_need(
        self, info: strawberry.Info, queue: PublicID, title: str, body: str, party: PublicID,
        client_creation_key: str, due_date: date | None = None, estimate: float | None = None,
        importance: NeedImportance = NeedImportance.NORMAL,  # type: ignore[valid-type]
    ) -> ActionResult:
        """Dispatch the atomic, replay-safe intake factory."""

        task = Need.objects.file_task(
            queue=authorized_permission_target(info, Queue, queue, "read"),
            party=authorized_permission_target(info, Party, party, "read"),
            title=title, body=body, client_creation_key=client_creation_key,
            due_date=due_date, estimate=estimate, importance=importance,
        )
        return ActionResult(ok=True, message="Task and need filed.", id=task.sqid)

    @strawberry.mutation
    @action_guard("Capture need failed.")
    def capture_need(
        self,
        info: strawberry.Info,
        target: NeedTargetInput,
        body: str,
        party: PublicID | None = None,
        importance: NeedImportance = NeedImportance.NORMAL,  # type: ignore[valid-type]
    ) -> ActionResult:
        """Idempotently capture one exact manual request on a task or project."""

        target_model = Need.objects.target_model(target.model_label)
        target_record = authorized_permission_target(info, target_model, target.record_id, "write")
        party_record = None if party is None else authorized_permission_target(info, Party, party, "read")
        need = Need.objects.capture(
            target=target_record,
            body=body,
            party=party_record,
            importance=importance,
        )
        return ActionResult(ok=True, message="Need captured.", id=need.sqid)

    @strawberry.mutation
    @action_guard("Convert need to task failed.")
    def convert_need_to_task(
        self,
        info: strawberry.Info,
        need: PublicID,
        queue: PublicID,
    ) -> ActionResult:
        """Convert one writable need into a triage task, or return its existing task."""

        target = authorized_permission_target(info, Need, need, "write")
        target_queue = authorized_permission_target(info, Queue, queue, "write")
        task = target.convert_to_task(target_queue)
        return ActionResult(ok=True, message="Need converted to task.", id=task.sqid)

    @strawberry.mutation
    @action_guard("Reset request access failed.")
    def reset_need_access(
        self, info: strawberry.Info, need: PublicID, confirmed: bool, expected_revision: int,
    ) -> ActionResult:
        """Reset the access decision without changing account credentials."""

        target = authorized_permission_target(info, Need, need, "write")
        target.reset_access(confirmed=confirmed, expected_revision=expected_revision)
        return ActionResult(ok=True, message="Request access reset.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Request access decision failed.")
    def decide_need_access(
        self,
        info: strawberry.Info,
        need: PublicID,
        action: NeedAccessAction,
        expected_revision: int | None = None,
    ) -> ActionResult:
        """Apply the need owner's access decision and return the approved account."""

        target = authorized_permission_target(info, Need, need, "write")
        user = target.decide_access(action, expected_revision=expected_revision)
        return ActionResult(ok=True, message="Request access decided.", id=user.sqid if user is not None else None)

    @strawberry.mutation
    @action_guard("Requester admission failed.")
    def admit_need_requester(self, info: strawberry.Info, need: PublicID, user: PublicID) -> ActionResult:
        """Assign the selected account, approve read, and follow in one act."""

        target = authorized_permission_target(info, Need, need, "write")
        account = authorized_permission_target(info, User, user, "read")
        target.admit_requester(account)
        return ActionResult(ok=True, message="Requester admitted.", id=target.sqid)

    @strawberry.mutation
    @action_guard("Requester removal failed.")
    def remove_need_requester(self, info: strawberry.Info, need: PublicID) -> ActionResult:
        """Remove the Need's requester seat and its decision-backed read."""

        target = authorized_permission_target(info, Need, need, "write")
        target.remove_requester()
        return ActionResult(ok=True, message="Requester removed.", id=target.sqid)


_NEED_RESOURCE = hasura_model_resource(
    NeedType,
    model=Need,
    name="intake_needs",
    filterable=[
        "id",
        "party",
        "task",
        "task__project",
        "project",
        "importance",
        "source_message",
        "original_task",
        "created_at",
        "updated_at",
    ],
    sortable=[
        "claimed_name",
        "party",
        "task",
        "project",
        "importance",
        "created_at",
        "updated_at",
    ],
    aggregatable=["id"],
    groupable=["party", "task", "project", "importance", "original_task"],
    insertable=["party", "task", "project", "importance", "body"],
    updatable=["party", "importance", "body"],
    field_id_decode={
        "party": public_pk_decoder(Party),
        "task": public_pk_decoder(Task),
        "task__project": public_pk_decoder(Project),
        "project": public_pk_decoder(Project),
        "source_message": public_pk_decoder(Message),
        "original_task": public_pk_decoder(Task),
    },
    write_backend=AngeeHasuraWriteBackend(
        Need,
        public_id_fields=("party", "task", "project"),
    ),
)

_INTAKE_SCHEMA_BUCKET = {
    "query": [_NEED_RESOURCE.query],
    "mutation": [IntakeActionMutation, _NEED_RESOURCE.mutation],
    "types": [
        NeedType,
        TaskRequester,
        ChannelType,
        MessageType,
        PartyType,
        ProjectType,
        TaskType,
        WorkQueueType,
        *_NEED_RESOURCE.types,
    ],
    "type_extensions": [ChannelIntakeExtension, TaskIntakeExtension],
    "input_extensions": [ProjectIntakeSetupInput],
}

schemas = {
    "public": {**_INTAKE_SCHEMA_BUCKET},
    "console": {
        **_INTAKE_SCHEMA_BUCKET,
        "type_extensions": [ChannelIntakeExtension, TaskIntakeExtension, ConsoleTaskIntakeExtension],
        "subscription": [changes(Need, field="intakeNeedChanged")],
    },
}
