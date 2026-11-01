"""The common persistence ancestor composed by foundation model capabilities."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Self

from django.db import models

from angee.base.querysets import ClosedUpdateQuerySet, row_lock_paths


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

    def bind_persistence_record(self, record: Self) -> Self:
        """Carry instance-owned persistence context onto a freshly loaded record."""
        return record

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Self:
        """Lock and reload this subject through its row-bound base manager."""

        del members
        model = type(self)
        manager = model._base_manager.db_manager(self._state.db, hints={"instance": self})
        return self.bind_persistence_record(manager.all().select_for_update(of=row_lock_paths(model)).get(pk=self.pk))

    @classmethod
    def system_queryset(
        cls,
        *,
        lock: tuple[str, ...] | None = None,
    ) -> models.QuerySet[Self]:
        """Return the declared queryset with the shared lock contract."""

        model = cls
        queryset = model._default_manager.get_queryset()
        if lock is None:
            return queryset
        return queryset.select_for_update(of=row_lock_paths(model, lock))

    def attributed_user_id(self) -> Any:
        """No user is implicit in a permission-neutral Django model."""
        return None

    @classmethod
    def retained_reference_update(cls, selected: models.QuerySet[Any], **values: Any) -> int:
        """Dispatch a Django collector update through this model's persistence owner."""
        rows = cls._base_manager.db_manager(selected.db, hints=selected._hints).get_queryset()
        rows.query = selected.query.chain()
        return rows.owner_update(**values) if isinstance(rows, ClosedUpdateQuerySet) else rows.update(**values)
