"""Django queryset write hooks with authoritative stored snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from itertools import batched
from typing import TYPE_CHECKING, Any, Self

from django.apps import apps
from django.db import NotSupportedError, connections, models, transaction
from simple_history.exceptions import NotHistoricalModelError
from simple_history.utils import get_history_manager_for_model

from angee.provenance.engine.replay import HistoryReplayMixin, HistoryScopeMixin

from .context import write_group

if TYPE_CHECKING:
    from angee.provenance.history import ModelHistory


def model_history(model: type[models.Model]) -> ModelHistory | None:
    try:
        manager = get_history_manager_for_model(model._meta.concrete_model)
    except NotHistoricalModelError:
        return None
    return getattr(manager.model, "_provenance_history", None)


class HistoryQuerySet[T: models.Model](models.QuerySet[T]):
    def capture_history(self) -> ModelHistory | None:
        owner = model_history(self.model)
        return owner if owner is not None and owner.capture_enabled else None

    def capture_queryset(self) -> Self:
        return self.select_for_update()

    def capture_keys(self) -> list[Any]:
        return list(self.order_by().values_list("pk", flat=True))

    def capture_batches(self, keys: Sequence[Any]) -> Iterator[Self]:
        size = connections[self.db].ops.bulk_batch_size([self.model._meta.pk], keys) or len(keys) or 1
        for chunk in batched(keys, size, strict=False):
            yield self.filter(pk__in=chunk)

    def update(self, **kwargs: Any) -> int:
        owner = self.capture_history()
        tracked = (
            {
                name
                for field in owner.fields_included(self.model)
                for name in (field.name, field.attname)
                if not getattr(field, "auto_now", False)
            }
            if owner
            else set()
        )
        if owner is None or self.query.is_sliced or not tracked.intersection(kwargs):
            return super().update(**kwargs)
        self._for_write = True
        with transaction.atomic(using=self.db), write_group():
            keys = list(dict.fromkeys(self.capture_queryset().capture_keys()))
            if not keys:
                return 0
            owner.require_context(owner.destination(self.model, self.db))
            result = sum(super(HistoryQuerySet, batch).update(**kwargs) for batch in self.capture_batches(keys))
            owner.bulk_history_create((self.model._meta.concrete_model or self.model), keys, "~", using=self.db)
            self._result_cache = None
            return result

    def bulk_create(
        self,
        objs: Iterable[T],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[T]:
        objects = list(objs)
        owner = self.capture_history()
        self._for_write = True
        if owner and objects:
            if ignore_conflicts:
                raise NotSupportedError("Captured inserts cannot identify ignored conflicts.")
            if not connections[self.db].features.can_return_rows_from_bulk_insert and any(
                obj.pk is None for obj in objects
            ):
                raise NotSupportedError("Captured inserts require returned primary keys.")
        with transaction.atomic(using=self.db), write_group():
            if owner and objects:
                owner.require_context(owner.destination(self.model, self.db))
            rows = super().bulk_create(
                objects,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=tuple(update_fields) if update_fields is not None else None,
                unique_fields=tuple(unique_fields) if unique_fields is not None else None,
            )
            if owner and rows:
                owner.bulk_history_create(
                    (self.model._meta.concrete_model or self.model),
                    [row.pk for row in rows],
                    "~" if update_conflicts else "+",
                    using=self.db,
                    instances={row.pk: row for row in rows},
                    batch_size=batch_size,
                )
            return rows

    def delete(self) -> tuple[int, dict[str, int]]:
        owner = self.capture_history()
        if owner is None or owner.cascade_delete_history or self.query.is_sliced:
            return super().delete()
        self._for_write = True
        with transaction.atomic(using=self.db), write_group():
            keys = list(dict.fromkeys(self.capture_queryset().capture_keys()))
            if not keys:
                return 0, {}
            owner.require_context(owner.destination(self.model, self.db))
            owner.bulk_history_create((self.model._meta.concrete_model or self.model), keys, "-", using=self.db)
            counts: dict[str, int] = {}
            for batch in self.capture_batches(keys):
                _, deleted = super(HistoryQuerySet, batch).delete()
                for label, count in deleted.items():
                    counts[label] = counts.get(label, 0) + count
            self._result_cache = None
            return sum(counts.values()), counts


class ChangesetQuerySet(models.QuerySet[Any]):
    def touching(self, record: Any) -> Any:
        scopes = [
            get_history_manager_for_model(model)
            .using(self.db)
            .filter(predicate)
            .order_by()
            .values_list("history_changeset_id", flat=True)
            for model, predicate in record.history_scopes().items()
        ]
        if not scopes:
            return self.none()
        return self.filter(pk__in=models.Subquery(scopes[0].union(*scopes[1:])))

    def records_touched(self) -> Any:
        models = (model for model in apps.get_models() if issubclass(model, HistoryReplayMixin))
        yield from self._records_touched(models)

    def _records_touched(
        self,
        document_models: Iterable[type[HistoryScopeMixin]],
        additional: Callable[[Any, Callable[[type[models.Model], str], set[Any]]], Iterable[Any]] | None = None,
    ) -> Any:
        """Invert declared documents once; hosts contribute actual additional retained identities."""
        touched = {label for labels in self.order_by().values_list("touched", flat=True) for label in labels}
        boundaries = self.order_by().values("pk")
        identities: dict[tuple[type[models.Model], str], set[Any]] = {}

        def touched_ids(table: type[models.Model], column: str) -> set[Any]:
            if table._meta.label_lower not in touched:
                return set()
            key = table, column
            if key not in identities:
                identities[key] = set(
                    get_history_manager_for_model(table)
                    .using(self.db)
                    .filter(history_changeset_id__in=boundaries)
                    .order_by()
                    .values_list(column, flat=True)
                )
            return identities[key]

        for model in sorted(document_models, key=lambda item: item._meta.label_lower):
            record = model()
            predicate = models.Q(pk__in=[])
            for table, columns in record.history_identity_columns().items():
                for column in columns:
                    field = record._history_identity_field(table, column)
                    predicate |= models.Q(**{f"{field.name}__in": touched_ids(table, column)})
            if additional is not None:
                predicate |= models.Q(pk__in=additional(record, touched_ids))
            rows = model._base_manager.using(self.db).filter(predicate)
            yield from ((model, pk) for pk in rows.order_by("pk").values_list("pk", flat=True))
