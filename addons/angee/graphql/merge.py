"""The shared GraphQL verb that merges records of any mergeable model into one survivor."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.core.exceptions import ValidationError
from rebac.resources import model_for_resource_type
from strawberry.scalars import JSON

from angee.base.merge import MergeableMixin
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID, public_id_value

MERGE_PERMISSION = "write"
"""The survivor permission the verb resolves through; ``absorb`` checks each merged record's ``delete``."""


@strawberry.type
class MergeMutation:
    """Merge verb for every model composing ``MergeableMixin``."""

    @strawberry.mutation
    @action_guard("Merging the records failed.")
    def merge_records(
        self,
        info: strawberry.Info,
        target_type: str,
        survivor_id: PublicID,
        ids: list[PublicID],
        confirm: bool,
        values: JSON | None = None,
    ) -> ActionResult:
        """Merge the confirmed records into the survivor, optionally setting its fields."""

        if not confirm:
            raise ValidationError({"confirm": "Confirm the change to apply it."})
        model = model_for_resource_type(target_type)
        if model is None or not issubclass(model, MergeableMixin):
            raise ValidationError({"target_type": f"Records of type {target_type!r} cannot be merged."})
        if values is not None and not isinstance(values, dict):
            raise ValidationError({"values": "Values must name fields of the record that is kept."})
        survivor = cast(
            MergeableMixin,
            authorized_permission_target(info, cast(Any, model), survivor_id, MERGE_PERMISSION),
        )
        survivor.absorb(records=[public_id_value(value) for value in ids], values=values)
        return ActionResult(ok=True, message="Merged.")
