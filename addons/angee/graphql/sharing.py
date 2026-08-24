"""Declarative GraphQL surface for direct record sharing."""

from __future__ import annotations

from typing import Any

import strawberry
from angee.base.models import AngeeModel, DirectRecordAccess, instance_from_public_id
from django.contrib.auth import get_user_model
from rebac.resources import model_for_resource_type

from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
from angee.graphql.ids import PublicID


@strawberry.type
class RecordAccessType:
    """One stored direct relation on a record's declared share surface."""

    relation: str
    subject: str

    @classmethod
    def from_direct(cls, access: DirectRecordAccess) -> RecordAccessType:
        """Project one model-owned direct tuple onto the GraphQL boundary."""

        return cls(relation=access.relation, subject=str(access.subject))


@strawberry.type
class RecordAccessQuery:
    """Direct-access listing for one declaratively shareable record."""

    @strawberry.field
    def record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
    ) -> list[RecordAccessType]:
        """Return direct declared-relation tuples, without effective expansion."""

        model = _shareable_model(target_type)
        permissions = sorted(set(model.get_rebac_grantable().values()))
        if not permissions:
            raise ValueError(f"{model._meta.label} declares no grantable relations.")
        target = authorized_action_target(info, model, target_id, permissions[0])
        return [RecordAccessType.from_direct(access) for access in target.direct_record_access()]


@strawberry.type
class RecordAccessMutation:
    """Authored grant and revoke actions for declared record relations."""

    @strawberry.mutation
    @action_guard("Grant record access failed.")
    def grant_record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
        relation: str,
        subject: PublicID,
    ) -> ActionResult:
        """Idempotently grant a user one relation declared by the target model."""

        model = _shareable_model(target_type)
        permission = model.record_access_permission(relation)
        target = authorized_action_target(info, model, target_id, permission)
        target.grant_record_access(relation, _subject_user(subject))
        return ActionResult(ok=True, message="Record access granted.")

    @strawberry.mutation
    @action_guard("Revoke record access failed.")
    def revoke_record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
        relation: str,
        subject: PublicID,
    ) -> ActionResult:
        """Idempotently revoke a user from one target-declared relation."""

        model = _shareable_model(target_type)
        permission = model.record_access_permission(relation)
        target = authorized_action_target(info, model, target_id, permission)
        target.revoke_record_access(relation, _subject_user(subject))
        return ActionResult(ok=True, message="Record access revoked.")


def _shareable_model(target_type: str) -> type[AngeeModel]:
    """Resolve one declared Angee record model from its REBAC resource type."""

    model = model_for_resource_type(target_type)
    if model is None or not issubclass(model, AngeeModel):
        raise ValueError(f"Record target type {target_type!r} is not shareable.")
    return model


def _subject_user(subject: PublicID) -> Any:
    """Resolve the recipient user through Angee's public-id path."""

    user = instance_from_public_id(get_user_model(), str(subject))
    if user is None:
        raise ValueError(f"User {str(subject)!r} was not found.")
    return user


schemas = {
    "console": {
        "query": [RecordAccessQuery],
        "mutation": [RecordAccessMutation],
        "types": [RecordAccessType],
    }
}
"""Direct record-sharing contributions to the console schema."""
