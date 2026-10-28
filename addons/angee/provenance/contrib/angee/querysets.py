"""Capture integration for Angee scoped and base querysets."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any, Self, cast

from django.db import models
from rebac.managers import RebacQuerySet
from rebac.watch import statement_keys

from angee.base.querysets import AngeeQuerySet, AngeeUnscopedQuerySet, MergeableUnscopedQuerySet, by_key, row_lock_paths
from angee.provenance.querysets import HistoryQuerySet


class AngeeHistoryQuerySet[T: models.Model](HistoryQuerySet[T], AngeeQuerySet[T]):
    """Capture guarded writes while reading full stored snapshots internally."""

    def capture_queryset(self) -> Self:
        rows = self._system_capture(reason="angee.history.rows") if isinstance(self, RebacQuerySet) else self
        return cast(Self, rows.select_for_update(of=row_lock_paths(self.model)))

    def capture_keys(self) -> list[Any]:
        return statement_keys(self)

    def capture_batches(self, keys: Sequence[Any]) -> Iterator[Self]:
        yield from by_key(self, keys)


class AngeeHistoryUnscopedQuerySet[T: models.Model](HistoryQuerySet[T], AngeeUnscopedQuerySet[T]):
    """Capture base-manager writes with their native tracked mutation policy."""

    def capture_queryset(self) -> Self:
        return self.select_for_update(of=row_lock_paths(self.model))

    def capture_keys(self) -> list[Any]:
        return statement_keys(self)

    def capture_batches(self, keys: Sequence[Any]) -> Iterator[Self]:
        yield from by_key(self, keys)


class AngeeHistoryMergeableUnscopedQuerySet[T: models.Model](
    AngeeHistoryUnscopedQuerySet[T], MergeableUnscopedQuerySet[T],
):
    """Compose native capture with an explicitly declared merge identity API."""
