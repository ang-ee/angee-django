"""Readable questions and their sole verdict mutation."""

from __future__ import annotations

from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from rebac.graphql.strawberry_django import optimize
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.auth.utils import get_current_user

from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.capabilities import permission_expression, permissions_field
from angee.graphql.data.hasura import declared_hasura_resource_fields, hasura_model_resource, public_pk_decoder
from angee.graphql.data.hasura import with_filter_aliases
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.node import AngeeNode
from angee.graphql.relations import (
    RecordReferenceNode,
    actor_scoped_to_many,
    actor_scoped_to_one,
    with_record_reference_access,
)
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
    verdict_values: JSON | None
    verdict_label: str = strawberry_django.field(only=["verdict", "proposal"])
    answered_by: UserType | None = actor_scoped_to_one("answered_by")
    answered_at: auto
    revision: auto
    created_at: auto
    updated_at: auto
    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["kind"])
    is_open: bool = strawberry_django.field(only=["verdict"])
    permissions = permissions_field(("act",))

    @strawberry_django.field(only=["id"])
    def concerns_label(self, info: strawberry.Info) -> str:
        """The readable records this question concerns, by their own labels (a bill number, a supplier).

        One read per decision when selected: the dashboard's attention table names the bill
        beside the question; the inbox list links the records through ``records`` instead.
        """
        actor = get_current_user(info)
        links = DecisionRecord.objects.with_actor(actor).filter(decision_id=cast(Any, self).pk)
        return "; ".join(str(link.record) for link in with_record_reference_access(links)
                         if link._angee_record_readable and link.record is not None)


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
class DecisionMutation:
    @strawberry.mutation
    @action_guard("Could not decide.", camel_case_keys=False)
    def decide(
        self, info: strawberry.Info, id: PublicID, chosen: list[str], revision: int, values: JSON | None = None,
    ) -> ActionResult:
        decision = authorized_permission_target(info, Decision, id, "act")
        decision = decision.decide(actor=get_current_user(info), revision=revision, chosen=chosen, values=values)
        return ActionResult(ok=True, message="Decision recorded.", id=decision.sqid)


@strawberry.type
class DecisionQuery:
    @strawberry_django.field
    def record_decision_answers(
        self, info: strawberry.Info, model: str, id: PublicID, limit: int = 25,
    ) -> list[DecisionType]:
        """Newest answered decisions attached to one actor-readable record."""

        actor = get_current_user(info)
        record_model = apps.get_model(model)
        record = require_instance_for_id(record_model, str(id), queryset=record_model.objects.with_actor(actor))
        queryset = (
            Decision.objects.with_actor(actor)
            .for_records(record)
            .exclude(answered_at=None)
            .order_by("-answered_at", "-pk")[: max(1, min(limit, 100))]
        )
        return list(optimize(DecisionType.get_queryset(queryset, info), info))


schemas = {"console": {
    "query": [DecisionQuery, _DECISIONS.query, _RECORDS.query],
    "mutation": [DecisionMutation],
    "subscription": [changes(Decision, field="decisionChanged")],
    "types": [DecisionType, DecisionRecordType, *_DECISIONS.types, *_RECORDS.types],
}}
