"""The shared GraphQL verb that merges records of any mergeable model into one survivor."""

from __future__ import annotations

from typing import Any, cast

import strawberry
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from rebac import PermissionDenied
from rebac.resources import model_for_resource_type

from angee.base.merge import MergeableMixin
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID, public_id_value


@strawberry.type
class MergeMutation:
    """Merge verb for every model composing ``MergeableMixin``."""

    @strawberry.mutation
    @action_guard("Merging the records failed.")
    def merge_records(
        self,
        info: strawberry.Info,
        target_type: str,
        target_id: PublicID,
        records: list[PublicID],
        confirm: bool,
    ) -> ActionResult:
        """Merge the confirmed ``records`` into the target record, which is kept."""

        if not confirm:
            raise ValidationError({"confirm": "Confirm the change to apply it."})
        model = model_for_resource_type(target_type)
        if model is None or not issubclass(model, MergeableMixin):
            raise ValidationError({"target_type": f"Records of type {target_type!r} cannot be merged."})
        target = cast(
            MergeableMixin,
            authorized_permission_target(info, cast(Any, model), target_id, model.MERGE_PERMISSION),
        )
        try:
            target.merge(records=[public_id_value(value) for value in records])
        except PermissionDenied as error:
            raise ValidationError({NON_FIELD_ERRORS: [str(error)]}) from error
        return ActionResult(ok=True, message="Merged.")
