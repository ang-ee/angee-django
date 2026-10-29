"""Native Django querysets preserving every stored state on captured writes."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import nullcontext
from itertools import batched
from typing import TYPE_CHECKING, Any, Self, TypeVar

from django.db import NotSupportedError, connections, models, transaction
from simple_history.exceptions import NotHistoricalModelError
from simple_history.utils import get_history_manager_for_model

from angee.provenance.context import current_changeset, write_group

if TYPE_CHECKING:
    from angee.provenance.history import ModelHistory

_ModelT = TypeVar("_ModelT", bound=models.Model)


def model_history(model: type[models.Model]) -> ModelHistory | None:
    """Read provenance's declaration from simple-history's native concrete-table registration."""

    concrete = model._meta.concrete_model or model
    try:
        manager = get_history_manager_for_model(concrete)
    except NotHistoricalModelError:
        return None
    return getattr(manager.model, "_provenance_history", None)


class HistoryQuerySet(models.QuerySet[_ModelT]):
    """Capture cooperative writes; downstream querysets keep their admission policy."""

    def capture_history(self) -> ModelHistory | None:
        """Apply the declaration's capture switch without hiding retained history."""

        history = model_history(self.model)
        return history if history is not None and history.capture_enabled else None

    def capture_queryset(self) -> Self:
        """Select stored identities under native row locks."""

        return self.select_for_update()

    def capture_keys(self) -> list[Any]:
        """Freeze the primary keys this write will change."""

        return list(self.order_by().values_list("pk", flat=True))

    def capture_batches(self, keys: Sequence[Any]) -> Iterator[Self]:
        """Use the database's native batch size for ordinary Django querysets.

        A filtered or permission-scoped adapter supplies its existing query-aware
        parameter budget through this hook.
        """

        size = connections[self.db].ops.bulk_batch_size([self.model._meta.pk], keys) or len(keys) or 1
        for chunk in batched(keys, size):
            yield self.filter(pk__in=chunk)

    def update(self, **kwargs: Any) -> int:
        """Capture stored state after an authorized write of frozen, locked keys.

        A write to columns the history excludes (a counter), with at most its
        timestamp stamps, changes no tracked state and records no version.
        """

        history_model = self.model._meta.concrete_model or self.model
        history = self.capture_history()
        if history is None or self.query.is_sliced or not self._changes_tracked_state(history, history_model, kwargs):
            return super().update(**kwargs)
        self._for_write = True
        selection = self.capture_queryset()
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(selection.capture_keys()))
            if not keys:
                return 0
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                count = sum(super(HistoryQuerySet, rows).update(**kwargs) for rows in self.capture_batches(keys))
                history.bulk_history_create(history_model, keys, "~", using=self.db)
                self._result_cache = None
                return count

    def _changes_tracked_state(
        self,
        history: ModelHistory,
        model: type[models.Model],
        values: Mapping[str, Any],
    ) -> bool:
        """Whether an update writes a column the history tracks, beyond auto-now stamps."""

        tracked = {field.attname for field in history.fields_included(model)} | {
            field.name for field in history.fields_included(model)
        }
        stamps = {
            name
            for field in model._meta.concrete_fields
            if getattr(field, "auto_now", False)
            for name in (field.name, field.attname)
        }
        return any(name in tracked for name in values if name not in stamps)

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Capture inserted/upserted stored rows, refusing unknowable identities."""

        history_model = self.model._meta.concrete_model or self.model
        history = self.capture_history()
        candidates = list(objs)
        self._for_write = True
        if history is not None and candidates:
            if ignore_conflicts:
                raise NotSupportedError("History capture cannot identify bulk_create(ignore_conflicts=True) rows.")
            if not connections[self.db].features.can_return_rows_from_bulk_insert and any(
                obj.pk is None for obj in candidates
            ):
                raise NotSupportedError("History capture requires bulk-insert primary keys on this database.")
        with transaction.atomic(using=self.db) if history is not None else nullcontext(), write_group():
            if history is not None and candidates:
                current_changeset(required=True, record=True)
            rows = super().bulk_create(
                candidates,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=tuple(update_fields) if update_fields is not None else None,
                unique_fields=tuple(unique_fields) if unique_fields is not None else None,
            )
            if history is not None and rows:
                if any(row.pk is None for row in rows):
                    raise NotSupportedError("History capture requires a primary key for every bulk-inserted row.")
                history.bulk_history_create(
                    history_model,
                    [row.pk for row in rows],
                    "~" if update_conflicts else "+",
                    using=self.db,
                    batch_size=batch_size,
                )
            return rows

    def delete(self) -> tuple[int, dict[str, int]]:
        """Batch root snapshots; native receivers retain collector cascade capture."""

        history_model = self.model._meta.concrete_model or self.model
        history = self.capture_history()
        if history is not None and history.cascade_delete_history:
            history = None
        if history is None or self.query.is_sliced:
            return super().delete()
        self._for_write = True
        selection = self.capture_queryset()
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(selection.capture_keys()))
            if not keys:
                return 0, {}
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                history.bulk_history_create(history_model, keys, "-", using=self.db)
                counts: dict[str, int] = {}
                for rows in self.capture_batches(keys):
                    _, deleted = super(HistoryQuerySet, rows).delete()
                    for label, count in deleted.items():
                        counts[label] = counts.get(label, 0) + count
                self._result_cache = None
                return sum(counts.values()), counts
