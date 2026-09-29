"""Read resources and subject-owned decision verbs for the inbox."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from rebac import current_actor
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import public_data_id_field
from angee.base.mixins import StaleRevisionError
from angee.base.refs import canonical_record_model
from angee.decisions.exceptions import RetryableDecisionError
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import declared_hasura_resource_fields, hasura_model_resource, public_pk_decoder
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_to_many, actor_scoped_to_one
from angee.graphql.subscriptions import changes
from angee.iam.schema import UserType

DecisionGroup = apps.get_model("decisions", "DecisionGroup")
Decision = apps.get_model("decisions", "Decision")
DecisionEvidence = apps.get_model("decisions", "DecisionEvidence")
DecisionVerdict = Decision._meta.get_field("verdict").choices_enum
strawberry.enum(cast(Any, DecisionVerdict), name="HumanDecisionVerdict")


@strawberry_django.type(DecisionGroup)
class DecisionGroupType(AngeeNode):
    """The settlement observed by a waiting owner."""

    policy: auto
    issuer: UserType | None = actor_scoped_to_one("issuer")
    settled_at: auto


@strawberry_django.type(Decision)
class HumanDecisionType(AngeeNode):
    """One seat's frozen question and final answer."""

    group: DecisionGroupType | None = actor_scoped_to_one("group")
    index: auto
    kind: auto
    requester: UserType | None = actor_scoped_to_one("requester")
    assignees: list[UserType] = actor_scoped_to_many("assignees")
    form_schema: JSON
    basis: JSON
    context: JSON
    verdict: auto
    closed_reason: auto
    superseded_by: HumanDecisionType | None = actor_scoped_to_one("superseded_by")
    resolution: JSON
    resolved_by: UserType | None = actor_scoped_to_one("resolved_by")
    resolved_at: auto
    invalid_attempts: auto
    max_attempts: auto
    expires_at: auto
    revision: auto
    created_at: auto
    updated_at: auto
    permissions = permissions_field(("act",))
    is_open: bool = strawberry_django.field(only=["verdict", "closed_reason"])
    @strawberry_django.field(annotate={
        "_can_revisit": lambda info: Decision.can_revisit_expression(current_actor()),
    })
    def can_revisit(self) -> bool:
        """Read subject-owned eligibility from the optimized query."""
        return cast(Any, self)._can_revisit

    record_model_label: str = strawberry_django.field(only=["subject_content_type_id", "subject_object_id"])
    record_public_id: str = strawberry_django.field(only=["subject_content_type_id", "subject_object_id"])
    display_name: str = strawberry_django.field(resolver=AngeeNode.display_name, only=["kind"])


@strawberry_django.type(DecisionEvidence)
class DecisionEvidenceType(AngeeNode):
    """A protected public record reference derived at admission."""

    decision: HumanDecisionType
    record_model_label: str = strawberry_django.field(only=["content_type_id", "object_id"])
    record_public_id: str = strawberry_django.field(only=["content_type_id", "object_id"])


_GROUPS = hasura_model_resource(
    DecisionGroupType, model=DecisionGroup, name="decision_groups", filterable=["id", "settled_at"],
    sortable=["id", "settled_at"], aggregatable=["id"], insert=False, update=False, delete=False,
)


def _subject_content_type(value: Any) -> int:
    """Resolve a declared subject model to the same canonical type as admission."""
    try:
        model = canonical_record_model(apps.get_model(str(value)))
    except (LookupError, ValueError):
        return -1
    return ContentType.objects.get_for_model(model).pk


def _subject_object_id(value: Any) -> int:
    """Let each public-id field decode its own prefixed subject identity."""
    public_id = str(value)
    for model in apps.get_models():
        field = public_data_id_field(model)
        if field is not None and field.prefix and public_id.startswith(field.prefix):
            return field.public_id_to_value(public_id) or -1
    return -1


_DECISIONS = hasura_model_resource(
    HumanDecisionType, model=Decision, name="decisions",
    filterable=["id", "group", "kind", "verdict", "closed_reason", "expires_at", "assignees", "requester",
                "subject_content_type", "subject_object_id",
                *declared_hasura_resource_fields(Decision, "hasura_filterable_fields")],
    sortable=["id", "index", "created_at", "expires_at"], aggregatable=["id"],
    groupable=["kind", "verdict", "closed_reason"], insert=False, update=False, delete=False,
    field_id_decode={
        "assignees": public_pk_decoder(Decision._meta.get_field("assignees").related_model),
        "subject_content_type": _subject_content_type,
        "subject_object_id": _subject_object_id,
    },
)
_EVIDENCE = hasura_model_resource(
    DecisionEvidenceType, model=DecisionEvidence, name="decision_evidence", filterable=["id", "decision"],
    sortable=["id"], aggregatable=["id"], insert=False, update=False, delete=False,
)


@strawberry.type
class HumanDecisionMutation:
    """Dispatch deciding through the exact action permission and manager."""

    @strawberry.mutation
    @action_guard("Could not revisit the decision.", camel_case_keys=False)
    def revisit_human_decision(self, info: strawberry.Info, id: PublicID, revision: int) -> ActionResult:
        """Ask the subject owner for a new seat, retaining the earlier answer."""
        decision = authorized_permission_target(info, Decision, id, "act")
        try:
            successor = decision.revisit(actor=info.context.request.user, revision=revision)
        except (StaleRevisionError, RetryableDecisionError) as error:
            raise ValidationError({"conflict": error.code}) from error
        return ActionResult(ok=True, message="Decision reopened for review.", id=successor.sqid)

    @strawberry.mutation
    @action_guard("Could not decide.", camel_case_keys=False)
    def decide_human_decision(
        self, info: strawberry.Info, id: PublicID, revision: int, action: str, values: JSON,
    ) -> ActionResult:
        """Record one action against the frozen form at the expected revision."""
        decision = authorized_permission_target(info, Decision, id, "act")
        try:
            decision = decision.decide(
                actor=info.context.request.user, revision=revision, action=action, values=values,
            )
        except (StaleRevisionError, RetryableDecisionError) as error:
            raise ValidationError({"conflict": error.code}) from error
        return ActionResult(ok=True, message="Decision recorded.", id=decision.sqid)


schemas = {
    "console": {
        "query": [_GROUPS.query, _DECISIONS.query, _EVIDENCE.query],
        "mutation": [HumanDecisionMutation],
        "subscription": [changes(Decision, field="humanDecisionChanged")],
        "types": [
            DecisionGroupType, HumanDecisionType, DecisionEvidenceType,
            *_GROUPS.types, *_DECISIONS.types, *_EVIDENCE.types,
        ],
    },
}
"""Console read resources, answer dispatch and subject-owned successor admission."""
