"""GraphQL resource and authored actions for external-request intake."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.data import AngeeHasuraWriteBackend, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_one
from angee.graphql.subscriptions import changes
from django.apps import apps
from strawberry import auto

from angee.messaging.schema import ChannelType, MessageType
from angee.parties.schema import PartyType
from angee.projects.schema import ProjectType, TaskType
from angee.work.schema import WorkQueueType

Need = apps.get_model("intake", "Need")
Channel = apps.get_model("messaging", "Channel")
Party = apps.get_model("parties", "Party")
Project = apps.get_model("projects", "Project")
Task = apps.get_model("projects", "Task")
Message = apps.get_model("messaging", "Message")
Queue = apps.get_model("work", "Queue")

NeedImportance = Need._meta.get_field("importance").choices_enum
strawberry.enum(cast(Any, NeedImportance))


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
    created_at: auto
    updated_at: auto

    party: PartyType | None = actor_scoped_to_one("party")
    task: TaskType | None = actor_scoped_to_one("task")
    project: ProjectType | None = actor_scoped_to_one("project")
    source_message: MessageType | None = actor_scoped_to_one("source_message")
    original_task: TaskType | None = actor_scoped_to_one("original_task")


@strawberry_django.type(Channel, name="ChannelType", extend=True)
class ChannelIntakeExtension:
    """Contribute intake configuration onto messaging's channel node."""

    intake_trigger: auto
    intake_queue: WorkQueueType | None = actor_scoped_to_one("intake_queue")


@strawberry.type
class IntakeActionMutation:
    """Row-authorized manual capture and Need-to-Task conversion actions."""

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
        target_record = authorized_action_target(info, target_model, target.record_id, "write")
        party_record = None if party is None else authorized_action_target(info, Party, party, "read")
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

        target = authorized_action_target(info, Need, need, "write")
        target_queue = authorized_action_target(info, Queue, queue, "write")
        task = target.convert_to_task(target_queue)
        return ActionResult(ok=True, message="Need converted to task.", id=task.sqid)


_NEED_RESOURCE = hasura_model_resource(
    NeedType,
    model=Need,
    name="intake_needs",
    filterable=[
        "id",
        "party",
        "task",
        "project",
        "importance",
        "source_message",
        "original_task",
        "created_at",
        "updated_at",
    ],
    sortable=[
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
        ChannelType,
        MessageType,
        PartyType,
        ProjectType,
        TaskType,
        WorkQueueType,
        *_NEED_RESOURCE.types,
    ],
    "type_extensions": [ChannelIntakeExtension],
}

schemas = {
    "public": {**_INTAKE_SCHEMA_BUCKET},
    "console": {
        **_INTAKE_SCHEMA_BUCKET,
        "subscription": [changes(Need, field="intakeNeedChanged")],
    },
}
