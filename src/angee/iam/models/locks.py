"""Native IAM authority composed onto the foundation row-lock invariant."""

from __future__ import annotations

from typing import Any, ClassVar, Self

from django.db import models
from rebac import RebacMixin
from rebac.actors import is_sudo
from rebac.managers import RebacQuerySet

from angee.base.models.locks import RowLockMixin, RowLockQuerySet
from angee.iam.querysets import ProtectedUnscopedQuerySet


class ProtectedRowLockQuerySet(RowLockQuerySet):
    """Preserve final actor write scoping and explicit native bypass."""

    def _lock_bypassed(self) -> bool:
        return self.effective_actor(strict=True)[1] if isinstance(self, RebacQuerySet) else is_sudo()

    def row_lock_write_scope(self) -> Self:
        return self.with_action("write").scoped() if isinstance(self, RebacQuerySet) else self


class _ProtectedRowLockBaseQuerySet(ProtectedRowLockQuerySet, ProtectedUnscopedQuerySet):
    """Native unfiltered tracked writes retaining row-lock guards."""


_ProtectedRowLockBaseManager = models.Manager.from_queryset(_ProtectedRowLockBaseQuerySet)


class ProtectedRowLockMixin(RowLockMixin, concrete=False):
    """Apply IAM's pinned authority to generic system-row invariants."""

    _angee_base: ClassVar[models.Manager[Self]] = _ProtectedRowLockBaseManager()  # type: ignore[assignment]  # Distinct from_queryset manager classes on the base and IAM mixins have incompatible nominal types.

    class Meta:
        abstract = True

    def _row_lock_bypassed(self) -> bool:
        return self.effective_actor()[1] if isinstance(self, RebacMixin) else is_sudo()

    def row_lock_delete_bypassed(self, origin: Any) -> bool:
        return origin.effective_actor(strict=True)[1] if isinstance(origin, (RebacQuerySet, RebacMixin)) else is_sudo()


class IAMRowLockMixin(ProtectedRowLockMixin, extends="base.RowLockMixin"):
    """Contribute authority without replacing a source's row-lock manager."""

    compose_querysets = {"objects": ProtectedRowLockQuerySet, "_angee_base": ProtectedRowLockQuerySet}

    class Meta:
        abstract = True
