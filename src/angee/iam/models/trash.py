"""IAM attribution for neutral trash persistence."""

from __future__ import annotations

from typing import Any

from rebac import actor_context

from angee.base.models.trash import TrashMixin
from angee.iam.models.base import ProtectedModel
from angee.iam.permissions import instance_actor
from angee.iam.querysets import ProtectedQuerySet, ProtectedUnscopedQuerySet


class ProtectedTrashMixin(TrashMixin, ProtectedModel, concrete=False):
    """Keep a pinned actor across a trash owner's freshly loaded record."""

    class Meta:
        abstract = True

    def revert_removal(self, previous: Any = None, *, reason: str = "") -> None:
        with actor_context(instance_actor(self)):
            super().revert_removal(previous, reason=reason)


class IAMTrashMixin(ProtectedTrashMixin, extends="base.TrashMixin"):
    """Donate actor attribution separately from protected source declaration ancestry."""

    compose_querysets = {"objects": ProtectedQuerySet, "_angee_base": ProtectedUnscopedQuerySet}

    class Meta:
        abstract = True
