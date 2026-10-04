"""Readable questions and their sole verdict mutation."""

from __future__ import annotations

from functools import partial

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac import current_actor
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.scoping import read_scoped_queryset
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.capabilities import permission_expression, permissions_field
from angee.graphql.data import declared_hasura_resource_fields, hasura_model_resource, public_pk_decoder
from angee.graphql.data.hasura import with_filter_aliases
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import RecordReferenceNode, actor_scoped_to_many, actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType

Decision = apps.get_model("decisions", "Decision")
DecisionRecord = apps.get_model("decisions", "DecisionRecord")


@strawberry_django.type(DecisionRecord)
class DecisionRecordType(RecordReferenceNode):
    decision: DecisionType = actor_scoped_to_one("decision")
    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


@strawberry_django.type(Decision)
class DecisionType(AngeeNode):
    @classmethod
    def get_queryset(cls, queryset: models.QuerySet, info: strawberry.Info) -> models.QuerySet:
        return with_filter_aliases(queryset)

    kind: auto
    kind_label: str = strawberry_django.field(only=["kind"])
    requester: UserType | None = actor_scoped_to_one("requester")
    assignees: list[UserType] = actor_scoped_to_many("assignees")
    context: JSON
    proposal: JSON
    records: list[DecisionRecordType] = actor_scoped_to_many("records")
    verdict: JSON | None
    verdict_label: str = strawberry_django.field(only=["verdict", "proposal"])
    answered_by: UserType | None = actor_scoped_to_one("answered_by")
    answered_at: auto
    revision: auto
    created_at: auto
    updated_at: auto
    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["kind"])
    is_open: bool = strawberry_django.field(only=["verdict"])
    permissions = permissions_field(("act",))


_DECISIONS = hasura_model_resource(
    DecisionType, model=Decision, name="decisions",
    filterable=["id", "kind", "assignees", "requester", "is_open", "can_act",
                *declared_hasura_resource_fields(Decision, "hasura_filterable_fields")],
    sortable=["id", "created_at"], aggregatable=["id"], groupable=["kind"],
    insert=False, update=False, delete=False,
    field_id_decode={"assignees": public_pk_decoder(Decision._meta.get_field("assignees").related_model)},
    filter_expressions={
        "is_open": Decision.objects.open_expression(),
        "can_act": partial(permission_expression, name="act"),
    },
)
_RECORDS = hasura_model_resource(
    DecisionRecordType, model=DecisionRecord, name="decision_records", filterable=["id", "decision"],
    sortable=["id"], aggregatable=["id"], insert=False, update=False, delete=False,
    record_ref_filters=("record_model", "record_id"), record_ref_requires_read=True,
)


@strawberry.type
class DecisionQuery:
    @strawberry_django.field
    def open_decisions(self, record_model: str, record_id: PublicID) -> list[DecisionType]:
        model = apps.get_model(record_model)
        actor = current_actor()
        record = require_instance_for_id(model, str(record_id), queryset=read_scoped_queryset(model, actor))
        return Decision.objects.with_actor(actor).open_for(record)


@strawberry.type
class DecisionMutation:
    @strawberry.mutation
    @action_guard("Could not decide.", camel_case_keys=False)
    def decide(self, info: strawberry.Info, id: PublicID, chosen: list[str], revision: int) -> ActionResult:
        decision = authorized_permission_target(info, Decision, id, "act")
        decision = decision.decide(actor=info.context.request.user, revision=revision, chosen=chosen)
        return ActionResult(ok=True, message="Decision recorded.", id=decision.sqid)


schemas = {"console": {
    "query": [_DECISIONS.query, _RECORDS.query, DecisionQuery],
    "mutation": [DecisionMutation],
    "subscription": [changes(Decision, field="decisionChanged")],
    "types": [DecisionType, DecisionRecordType, *_DECISIONS.types, *_RECORDS.types],
}}
