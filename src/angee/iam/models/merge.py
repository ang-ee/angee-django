"""Native IAM authorization for the foundation merge owner."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Self

from django.core.exceptions import ValidationError
from django.db import models
from rebac import PermissionDenied, actor_context, system_context

from angee.base.models.merge import MERGE_REASON, MergeableMixin
from angee.base.models.persistence import require_composed_type
from angee.base.querysets import MergeableQuerySet
from angee.iam.models.base import ProtectedModel
from angee.iam.permissions import instance_actor
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet
from angee.iam.refs import named_in_relationships


class ProtectedMergeableUnscopedQuerySet(MergeableQuerySet, ProtectedUnscopedQuerySet):
    """Preserve merge identity and native tracked unfiltered writes."""


_ProtectedMergeableBaseManagerBase = models.Manager.from_queryset(ProtectedMergeableUnscopedQuerySet)


class ProtectedMergeableUnscopedManager(_ProtectedMergeableBaseManagerBase):
    """Preserve unfiltered native merge identity and tracked writes."""


class ProtectedMergeableMixin(MergeableMixin, ProtectedModel, concrete=False):
    """Contribute pinned access checks while composing one merge implementation."""

    _angee_base = ProtectedMergeableUnscopedManager()

    class Meta:
        abstract = True

    def merge(self, *, records: Sequence[str], **options: Any) -> Self:
        with actor_context(instance_actor(self)):
            return super().merge(records=records, **options)

    def merge_candidates(self) -> ProtectedQuerySet[Self]:
        rows = require_composed_type(super().merge_candidates(), ProtectedQuerySet)
        rows = rows.with_action("read")
        actor, elevated = self.effective_actor()
        return rows.sudo(reason="iam.merge.admitted") if elevated else rows.with_actor(actor)

    def fold(self, merged: Sequence[Self], **options: Any) -> Self:
        if not self.effective_actor()[1]:
            raise PermissionDenied("A system fold requires a system boundary.")
        return super().fold(merged, **options)

    def reference_move_context(self):
        return system_context(reason=MERGE_REASON)

    def validate_merge(self, merged: Sequence[Self]) -> None:
        if not self.effective_actor()[1]:
            if not self.has_access(self.SURVIVOR_PERMISSION):
                raise PermissionDenied("You are not allowed to change the record that is kept.")
            if not all(record.has_access(self.MERGED_PERMISSION) for record in merged):
                raise PermissionDenied("You are not allowed to remove every record being merged.")
        if not all(self.retains(record) for record in merged):
            for record in merged:
                if named_in_relationships(record):
                    raise ValidationError(f"{record} is shared or named in access rules; remove that first.")
        super().validate_merge(merged)


class IAMMergeableMixin(ProtectedMergeableMixin, extends="base.MergeableMixin"):
    """Compose merge authority with existing default and base manager factories."""

    compose_querysets = {"objects": ProtectedQuerySet, "_angee_base": ProtectedUnscopedQuerySet}

    class Meta:
        abstract = True
