"""Read resources and the human deciding action for the inbox."""

from __future__ import annotations

from functools import partial

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from strawberry import auto
from strawberry.scalars import JSON

from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.capabilities import permission_expression, permissions_field
from angee.graphql.data import declared_hasura_resource_fields, hasura_model_resource, public_pk_decoder
from angee.graphql.data.hasura import with_filter_aliases
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.relations import RecordReferenceNode, actor_scoped_to_many, actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType

DecisionGroup = apps.get_model("decisions", "DecisionGroup")
Decision = apps.get_model("decisions", "Decision")
DecisionEvidence = apps.get_model("decisions", "DecisionEvidence")


@strawberry_django.type(DecisionGroup)
class DecisionGroupType(AngeeNode):
    """The settlement observed by a waiting owner."""

    policy: auto
    issuer: UserType | None = actor_scoped_to_one("issuer")
    settled_at: auto


@strawberry_django.type(Decision)
class DecisionType(RecordReferenceNode):
    """One seat's frozen question and final answer."""

    @classmethod
    def get_queryset(cls, queryset: models.QuerySet, info: strawberry.Info) -> models.QuerySet:
        """Compose extension-owned scalar aliases through native nested loading."""
        return with_filter_aliases(super().get_queryset(queryset, info))

    group: DecisionGroupType | None = actor_scoped_to_one("group")
    index: auto
    kind: auto
    kind_label: str = strawberry_django.field(only=["kind"])
    requester: UserType | None = actor_scoped_to_one("requester")
    assignees: list[UserType] = actor_scoped_to_many("assignees")
    form_schema: JSON
    basis: JSON
    context: JSON
    errors: JSON
    verdict: auto
    closed_reason: auto
    superseded_by: DecisionType | None = actor_scoped_to_one("superseded_by")
    resolution: JSON
    resolved_by: UserType | None = actor_scoped_to_one("resolved_by")
    resolved_at: auto
    invalid_attempts: auto
    max_attempts: auto
    expires_at: auto
    revision: auto
    created_at: auto
    updated_at: auto
    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["kind"])
    is_open: bool = strawberry_django.field(annotate={"_is_open": Decision.objects.open_expression()})
    permissions = permissions_field(("act",))

    subject_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["subject_content_type_id", "subject_object_id"],
    )
    subject_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["subject_content_type_id", "subject_object_id"],
    )


@strawberry_django.type(DecisionEvidence)
class DecisionEvidenceType(RecordReferenceNode):
    """A protected public record reference derived at admission."""

    decision: DecisionType = actor_scoped_to_one("decision")

    record_model: str | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_model, only=["content_type_id", "object_id"],
    )
    record_id: PublicID | None = strawberry_django.field(
        resolver=RecordReferenceNode.reference_id, only=["content_type_id", "object_id"],
    )


_GROUPS = hasura_model_resource(
    DecisionGroupType, model=DecisionGroup, name="decision_groups", filterable=["id", "settled_at"],
    sortable=["id", "settled_at"], aggregatable=["id"], insert=False, update=False, delete=False,
)
_DECISIONS = hasura_model_resource(
    DecisionType, model=Decision, name="decisions",
    filterable=["id", "group", "kind", "verdict", "closed_reason", "expires_at", "assignees", "requester",
                "is_open", "can_act", *declared_hasura_resource_fields(Decision, "hasura_filterable_fields")],
    sortable=["id", "index", "created_at", "expires_at"], aggregatable=["id"],
    groupable=["kind", "verdict", "closed_reason"], insert=False, update=False, delete=False,
    field_id_decode={"assignees": public_pk_decoder(Decision._meta.get_field("assignees").related_model)},
    filter_expressions={
        "is_open": Decision.objects.open_expression(),
        "can_act": partial(permission_expression, name="act"),
    },
    record_ref_filters=("subject_model", "subject_id"), record_ref_requires_read=True,
)
_EVIDENCE = hasura_model_resource(
    DecisionEvidenceType, model=DecisionEvidence, name="decision_evidence", filterable=["id", "decision"],
    sortable=["id"], aggregatable=["id"], insert=False, update=False, delete=False,
)


@strawberry.type
class DecisionMutation:
    """Dispatch deciding through the exact action permission and decision instance."""

    @strawberry.mutation
    @action_guard("Could not decide.", camel_case_keys=False)
    def decide(self, info: strawberry.Info, id: PublicID, revision: int, action: str, values: JSON) -> ActionResult:
        """Record one action against the frozen form at the expected revision."""
        decision = authorized_permission_target(info, Decision, id, "act")
        decision = decision.decide(actor=info.context.request.user, revision=revision, action=action, values=values)
        return ActionResult(ok=True, message="Decision recorded.", id=decision.sqid)


schemas = {
    "console": {
        "query": [_GROUPS.query, _DECISIONS.query, _EVIDENCE.query],
        "mutation": [DecisionMutation],
        "subscription": [changes(Decision, field="decisionChanged")],
        "types": [
            DecisionGroupType, DecisionType, DecisionEvidenceType, *_GROUPS.types, *_DECISIONS.types, *_EVIDENCE.types,
        ],
    },
}
"""Console read resources and the sole human answer mutation."""
