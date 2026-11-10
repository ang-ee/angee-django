"""The common persistence ancestor composed by foundation model capabilities."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar, Self

from django.db import models
from rebac.managers import RebacQuerySet

from angee.base.querysets import row_lock_paths


class PersistenceMixin(models.Model):
    """Model-owned persistence reads and shared cooperative lifecycle terminals."""

    owned_relations: ClassVar[tuple[str, ...]] = ()
    """Reverse foreign-key collections whose rows are parts of this record."""

    class Meta:
        abstract = True

    @classmethod
    @contextmanager
    def initialize_rows(cls, rows: Sequence[models.Model]) -> Iterator[bool]:
        """Bracket final derived creation values; observers may defer intermediate writes.

        The terminal lifecycle has no observer. A cooperative capability yields
        true while it owns one final observation after successful initialization.
        """

        del rows
        yield False

    @classmethod
    def retained_reference_exists(cls, lookup: Mapping[str, Any]) -> bool:
        """Whether an owned retained representation still names this target."""

        del lookup
        return False

    def prepare_lock_subject(self, values: Mapping[str, Any]) -> Self:
        """Hydrate pending concrete values solely for this model's lock selection.

        Native construction permits guarded state hydration without invoking a
        transition or mutating the saved row. Deferred fields stay deferred;
        relation names use Django's object descriptor and attnames carry IDs.
        Callers must reload and authorize the persisted row after locking.
        """

        columns = {}
        for field in self._meta.concrete_fields:
            if field.name in values:
                columns[field.name] = values[field.name]
            elif field.attname in values:
                columns[field.attname] = values[field.attname]
            else:
                columns[field.attname] = self.__dict__.get(field.attname, models.DEFERRED)
        candidate = type(self)(**columns)
        candidate._state.adding = self._state.adding
        return candidate

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Self:
        """Lock and reload this subject through its base manager."""

        del members
        model = type(self)
        return model._base_manager.select_for_update(of=row_lock_paths(model)).get(pk=self.pk)

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
