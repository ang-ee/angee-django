"""Shared GraphQL verbs that move any trashable record to the trash and back."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.core.exceptions import ValidationError
from rebac.resources import model_for_resource_type

from angee.base.models.trash import TrashQuerySet
from angee.graphql.actions import ActionResult, action_guard
from angee.graphql.ids import PublicID
from angee.iam.graphql.actions import authorized_permission_target
from angee.iam.models.trash import ProtectedTrashMixin

TRASH_PERMISSION = "delete"
"""The record permission that moves a record to the trash and restores it."""


@strawberry.type
class TrashMutation:
    """Trash and restore verbs for every model composing ``ProtectedTrashMixin``.

    The record's ``delete`` permission authorizes both. Its Zed decides who
    still reads a trashed record, so restoring re-grants nothing.
    """

    @strawberry.mutation
    @action_guard("Moving the record to the trash failed.")
    def trash_record(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
        confirm: bool,
        reason: str = "",
    ) -> ActionResult:
        """Move one confirmed record to the trash with an optional reason."""

        trash_target(info, target_type, target_id, confirm=confirm).trash(reason=reason)
        return ActionResult(ok=True, message="Moved to the trash.")

    @strawberry.mutation
    @action_guard("Restoring the record failed.")
    def restore_record(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
        confirm: bool,
    ) -> ActionResult:
        """Take one confirmed record out of the trash."""

        trash_target(info, target_type, target_id, confirm=confirm).restore()
        return ActionResult(ok=True, message="Restored from the trash.")


def trash_target(info: strawberry.Info, target_type: str, target_id: PublicID, *, confirm: bool) -> ProtectedTrashMixin:
    """Resolve a confirmed trash-verb target through the actor's ``delete`` scope."""

    if not confirm:
        raise ValidationError({"confirm": "Confirm the change to apply it."})
    model = model_for_resource_type(target_type)
    if model is None or not issubclass(model, ProtectedTrashMixin) or not model.stores_trash():
        raise ValidationError({"target_type": f"Records of type {target_type!r} cannot be trashed."})
    return cast(
        ProtectedTrashMixin,
        authorized_permission_target(
            info,
            cast(Any, model),
            target_id,
            TRASH_PERMISSION,
            narrow=lambda rows: cast(TrashQuerySet[Any], rows).trash_targets(),
        ),
    )
