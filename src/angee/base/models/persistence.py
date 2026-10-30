"""The common persistence ancestor composed by foundation model capabilities."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Self

from django.db import models
from rebac.managers import RebacQuerySet

from angee.base.querysets import row_lock_paths


class PersistenceMixin(models.Model):
    """Model-owned persistence reads and shared cooperative lifecycle terminals."""

    class Meta:
        abstract = True

    @classmethod
    @contextmanager
    def initialize_rows(cls, rows: Sequence[models.Model], *, using: str) -> Iterator[bool]:
        """Bracket final derived creation values; observers may defer intermediate writes.

        The terminal lifecycle has no observer. A cooperative capability yields
        true while it owns one final observation after successful initialization.
        """

        del rows, using
        yield False

    @classmethod
    def retained_reference_exists(cls, record: models.Model, lookup: Mapping[str, Any]) -> bool:
        """Whether an owned retained representation still names this target."""

        del record, lookup
        return False

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Self:
        """Lock and reload this subject through its row-bound base manager."""

        del members
        model = type(self)
        manager = model._base_manager.db_manager(self._state.db, hints={"instance": self})
        return manager.all().select_for_update(of=row_lock_paths(model)).get(pk=self.pk)

    @classmethod
    def system_queryset(
        cls,
        *,
        lock: tuple[str, ...] | None = None,
    ) -> models.QuerySet[Self]:
        """Return an elevated unscoped queryset with the shared lock contract."""

        model = cls
        queryset = model._default_manager.get_queryset()
        if isinstance(queryset, RebacQuerySet):
            queryset = queryset.system_context(reason=f"{model._meta.label_lower}.system_queryset")
        if lock is None:
            return queryset
        return queryset.select_for_update(of=row_lock_paths(model, lock))
