"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Self, TypeVar, cast

from django.conf import settings
from django.db import NotSupportedError, connections, models, transaction
from rebac.managers import RebacQuerySet
from simple_history.models import registered_models
from simple_history.utils import get_history_manager_for_model

from angee.base.changesets import current_changeset, write_group
from angee.base.scoping import lock_if_supported

_ModelT = TypeVar("_ModelT", bound=models.Model)


def model_history(model: type[models.Model]) -> Any | None:
    """Resolve the native registration, excluding history tables and Changeset."""

    if model._meta.label_lower == getattr(settings, "ANGEE_CHANGESET_MODEL", "").lower():
        return None
    if registered_models.get(model._meta.db_table) is not model:
        return None
    manager = get_history_manager_for_model(model)
    return manager.model._history_records()


class _AngeeQuerySetMixin(models.QuerySet[_ModelT]):
    """Shared lookup, locking and history capture, preserving each manager's policy.

    Bulk updates dispatch through Django's native update batches. Each executed
    batch opens its own write group, so repeated rows across batches retain every
    stored state. SQL execution always delegates to the downstream REBAC owner.
    """

    model: type[_ModelT]

    def _history_keys(self) -> list[Any]:
        """Freeze write targets without turning a write gate into a read scope."""

        selection = self
        if isinstance(self, RebacQuerySet):
            selection = self._system_capture(reason="angee.history.rows")
        return list(dict.fromkeys(selection.lock_if_supported().order_by().values_list("pk", flat=True)))

    def update(self, **kwargs: Any) -> int:
        """Capture stored state after a frozen, authorized update."""

        history = model_history(self.model)
        if history is None or self.query.is_sliced:
            return super().update(**kwargs)
        self._for_write = True
        current_changeset(required=True)
        with transaction.atomic(using=self.db), write_group():
            keys = self._history_keys()
            rows = self.filter(pk__in=keys)
            count = super(_AngeeQuerySetMixin, rows).update(**kwargs)
            if count:
                history.bulk_history_create(self.model, keys, "~", using=self.db)
            self._result_cache = None
            return count

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

        history = model_history(self.model)
        candidates = list(objs)
        if history is not None and candidates:
            if ignore_conflicts:
                raise NotSupportedError("History capture cannot identify bulk_create(ignore_conflicts=True) rows.")
            if not connections[self.db].features.can_return_rows_from_bulk_insert and any(
                obj.pk is None for obj in candidates
            ):
                raise NotSupportedError("History capture requires bulk-insert primary keys on this database.")
            current_changeset(required=True)
        self._for_write = True
        with transaction.atomic(using=self.db), write_group():
            rows = super().bulk_create(
                candidates, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts, update_fields=update_fields, unique_fields=unique_fields,
            )
            if history is not None and rows:
                if any(row.pk is None for row in rows):
                    raise NotSupportedError("History capture requires a primary key for every bulk-inserted row.")
                history.bulk_history_create(
                    self.model, [row.pk for row in rows], "~" if update_conflicts else "+",
                    using=self.db, batch_size=batch_size,
                )
            return rows

    def delete(self) -> tuple[int, dict[str, int]]:
        """Batch root snapshots; native receivers retain collector cascade capture."""

        history = model_history(self.model)
        if history is None or self.query.is_sliced:
            return super().delete()
        self._for_write = True
        current_changeset(required=True)
        with transaction.atomic(using=self.db), write_group():
            keys = self._history_keys()
            history.bulk_history_create(self.model, keys, "-", using=self.db)
            rows = self.filter(pk__in=keys)
            result = super(_AngeeQuerySetMixin, rows).delete()
            self._result_cache = None
            return result

    def from_public_id(self, value: str) -> _ModelT | None:
        """Return the row addressed by ``value`` within this queryset policy."""

        if value == "":
            return None
        try:
            lookup = cast(Any, self.model).public_id_lookup(value)
            return cast(_ModelT | None, cast(Any, self).filter(**lookup).first())
        except TypeError, ValueError:
            return None

    def lock_if_supported(
        self, *, of: tuple[str, ...] = ("self",), skip_locked: bool = False, no_key: bool = False
    ) -> Self:
        """Expose shared lock intent on Angee querysets and managers."""

        return cast(
            Self,
            lock_if_supported(cast(models.QuerySet[_ModelT], self), of=of, skip_locked=skip_locked, no_key=no_key),
        )

    def locked_get(self, *args: Any, **kwargs: Any) -> _ModelT:
        """Return one row under a database row lock when the backend supports it."""

        return cast(models.QuerySet[_ModelT], self.lock_if_supported()).get(*args, **kwargs)
