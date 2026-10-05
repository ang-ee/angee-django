"""Shared GraphQL verbs that move any trashable record to the trash and back."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.core.exceptions import ValidationError
from rebac.resources import model_for_resource_type

from angee.base.mixins import TrashMixin, TrashQuerySet
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID

TRASH_PERMISSION = "delete"
"""The record permission that moves a record to the trash and restores it."""


@strawberry.type
class TrashMutation:
    """Trash and restore verbs for every model composing ``TrashMixin``.

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


def trash_target(info: strawberry.Info, target_type: str, target_id: PublicID, *, confirm: bool) -> TrashMixin:
    """Resolve a confirmed trash-verb target through the actor's ``delete`` scope."""

    if not confirm:
        raise ValidationError({"confirm": "Confirm the change to apply it."})
    model = model_for_resource_type(target_type)
    if model is None or not issubclass(model, TrashMixin) or not model.stores_trash():
        raise ValidationError({"target_type": f"Records of type {target_type!r} cannot be trashed."})
    return cast(
        TrashMixin,
        authorized_permission_target(
            info,
            cast(Any, model),
            target_id,
            TRASH_PERMISSION,
            narrow=lambda rows: cast(TrashQuerySet[Any], rows).trash_targets(),
        ),
    )
