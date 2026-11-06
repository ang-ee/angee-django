"""Readable questions and their sole verdict mutation."""

from __future__ import annotations

from functools import partial
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models import Prefetch, prefetch_related_objects
from rebac.graphql.strawberry_django import optimize
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django.auth.utils import get_current_user

from angee.graphql.actions import ActionResult, action_guard
from angee.graphql.data.hasura import (
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
    with_filter_aliases,
)
from angee.graphql.ids import PublicID, require_instance_for_id
from angee.graphql.subscriptions import changes
from angee.iam.graphql.actions import authorized_permission_target
from angee.iam.graphql.capabilities import permission_expression, permissions_field
from angee.iam.graphql.node import ProtectedNode
from angee.iam.graphql.relations import (
    RecordReferenceNode,
    actor_scoped_to_many,
    actor_scoped_to_one,
    record_reference_field,
    with_record_reference_access,
)
from angee.iam.schema import UserType

Decision = apps.get_model("decisions", "Decision")
DecisionRecord = apps.get_model("decisions", "DecisionRecord")
_RECORDS_PREFETCH = Prefetch("records", queryset=DecisionRecord._base_manager.order_by("pk"))


def _concern_links(info: strawberry.Info) -> Prefetch:
    """Batch readable concern links and their generic targets for label projection."""
    actor = get_current_user(info)
    links = with_record_reference_access(
        DecisionRecord.objects.with_actor(actor).with_action("read"), select=False,
    ).filter(_angee_record_readable=True).prefetch_related("record")
    return Prefetch("records", queryset=links, to_attr="_concern_links")


def _admitted_records(root: models.Model) -> list[Any]:
    """Project concern links already bounded by their admitted decision."""
    cached = getattr(root, "_prefetched_objects_cache", {}).get("records")
    if cached is None:
        # A direct to-one fallback has no page optimizer. Django fills the
        # same declared cache in one read.
        prefetch_related_objects([root], _RECORDS_PREFETCH)
        cached = getattr(root, "_prefetched_objects_cache", {}).get("records")
    if isinstance(cached, models.QuerySet) and cached._result_cache is not None:
        return cached._result_cache
    raise ImproperlyConfigured("DecisionType.records could not populate its declared prefetch.")


@strawberry_django.type(DecisionRecord)
class DecisionRecordType(RecordReferenceNode):
    decision: DecisionType = actor_scoped_to_one("decision")
    record_label: str | None = record_reference_field(
        DecisionRecord, label=True, resolver=RecordReferenceNode.reference_label,
    )
    record_model: str | None = record_reference_field(DecisionRecord, resolver=RecordReferenceNode.reference_model)
    record_id: PublicID | None = record_reference_field(DecisionRecord, resolver=RecordReferenceNode.reference_id)


@strawberry_django.type(Decision)
class DecisionType(ProtectedNode):
    @classmethod
    def get_queryset(cls, queryset: models.QuerySet, info: strawberry.Info) -> models.QuerySet:
        return with_filter_aliases(queryset)

    kind: auto
    kind_label: str = strawberry_django.field(only=["kind"])
    requester: UserType | None = actor_scoped_to_one("requester")
    assignees: list[UserType] = actor_scoped_to_many("assignees")
    context: JSON
    proposal: JSON
    records: list[DecisionRecordType] = strawberry_django.field(
        resolver=_admitted_records,
        field_name="records",
        prefetch_related=[_RECORDS_PREFETCH],
    )
    verdict: JSON | None
    verdict_values: JSON | None
    verdict_label: str = strawberry_django.field(only=["verdict", "proposal"])
    answered_by: UserType | None = actor_scoped_to_one("answered_by")
    answered_at: auto
    revision: auto
    created_at: auto
    updated_at: auto
    is_open: bool = strawberry_django.field(only=["verdict"])
    permissions = permissions_field(("act",))

    @strawberry_django.field(prefetch_related=[_concern_links])
    def concerns_label(self, info: strawberry.Info) -> str:
        """Label readable concern targets from the selected decision batch."""
        row = cast(models.Model, self)
        if not hasattr(row, "_concern_links"):
            prefetch_related_objects([row], _concern_links(info))
        return "; ".join(str(link.record) for link in cast(Any, row)._concern_links if link.record is not None)


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
    record_ref_filters=("record_model", "record_id"),
    record_ref_requires_read=True,
)


@strawberry.type
class DecideResult(ActionResult):
    """The accepted decision projection alongside the action's refusal contract."""

    decision_row: strawberry.Private[Any] = None

    @strawberry.field
    def decision(self, info: strawberry.Info) -> DecisionType | None:
        """Return the answer only through the caller's optimized read scope."""
        if self.decision_row is None:
            return None
        actor = get_current_user(info)
        rows = (
            Decision.objects.db_manager(self.decision_row._state.db)
            .with_actor(actor).filter(pk=self.decision_row.pk)
        )
        return optimize(DecisionType.get_queryset(rows, info), info).first()


@strawberry.type
class DecisionMutation:
    @strawberry.mutation
    @action_guard("Could not decide.", camel_case_keys=False, result_type=DecideResult)
    def decide(
        self, info: strawberry.Info, id: PublicID, chosen: list[str], revision: int, values: JSON | None = None,
    ) -> DecideResult:
        decision, act_proof = authorized_permission_target(info, Decision, id, "act", with_proof=True)
        actor = get_current_user(info)
        answered = decision.decide(actor=actor, revision=revision, chosen=chosen, values=values, act_proof=act_proof)
        return DecideResult(ok=True, message="Decision recorded.", id=answered.sqid, decision_row=answered)


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
        # The proved record bounds decision identities through the same concern
        # links the timeline uses. A second decision read would expand unrelated
        # concerned-record policy into this page.
        concerns = DecisionRecord._base_manager.filter(DecisionRecord.record_filter(record)).values("decision_id")
        queryset = (
            Decision._base_manager.filter(pk__in=concerns)
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
