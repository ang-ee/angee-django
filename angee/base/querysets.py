"""Query conveniences shared by scoped and explicitly unscoped managers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, Self, TypeVar, cast

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import NotSupportedError, connections, models, transaction
from rebac import app_settings, system_context
from rebac.managers import RebacQuerySet, TrackedQuerySet
from rebac.watch import statement_keys

from angee.base.autoconfig import SETTINGS
from angee.base.changesets import current_changeset, write_group
from angee.base.scoping import by_key, lock_if_supported

if TYPE_CHECKING:
    from angee.base.mixins import ModelHistory

_ModelT = TypeVar("_ModelT", bound=models.Model)


def model_history(model: type[models.Model]) -> ModelHistory | None:
    """Resolve the concrete registration, refusing tracked proxies in strict mode."""

    if not getattr(settings, "SIMPLE_HISTORY_ENABLED", True):
        return None
    if (
        model._meta.label_lower
        == cast(
            str,
            getattr(
                settings,
                "ANGEE_CHANGESET_MODEL",
                SETTINGS["ANGEE_CHANGESET_MODEL"],
            ),
        ).lower()
    ):
        return None
    concrete = model._meta.concrete_model
    history = getattr(concrete._meta, "angee_model_history", None)
    if history is None:
        history = next(
            (
                getattr(parent._meta, "angee_model_history", None)
                for parent in concrete._meta.all_parents
                if getattr(parent._meta, "angee_model_history", None) is not None
            ),
            None,
        )
    if history is not None and model._meta.proxy:
        if app_settings.REBAC_STRICT_MODE:
            raise NotSupportedError("History capture does not support proxy model writes.")
        return None
    return cast("ModelHistory | None", history)


class _AngeeQuerySetMixin(models.QuerySet[_ModelT]):
    """Shared lookup, locking and history capture, preserving each manager's policy.

    Bulk updates dispatch through Django's native update batches. Each executed
    batch opens its own write group, so repeated rows across batches retain every
    stored state. SQL execution always delegates to the downstream REBAC owner.
    """

    model: type[_ModelT]

    def update(self, **kwargs: Any) -> int:
        """Capture stored state after an authorized write of frozen, locked keys."""

        history = model_history(self.model)
        if history is None or self.query.is_sliced:
            return super().update(**kwargs)
        self._for_write = True
        selection = self._system_capture(reason="angee.history.rows") if isinstance(self, RebacQuerySet) else self
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(statement_keys(lock_if_supported(selection))))
            if not keys:
                return 0
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                count = sum(super(_AngeeQuerySetMixin, rows).update(**kwargs) for rows in by_key(self, keys))
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
            current_changeset(required=True, record=True)
        self._for_write = True
        with transaction.atomic(using=self.db), write_group():
            rows = super().bulk_create(
                candidates,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )
            if history is not None and rows:
                if any(row.pk is None for row in rows):
                    raise NotSupportedError("History capture requires a primary key for every bulk-inserted row.")
                history.bulk_history_create(
                    self.model,
                    [row.pk for row in rows],
                    "~" if update_conflicts else "+",
                    using=self.db,
                    batch_size=batch_size,
                )
            return rows

    def delete(self) -> tuple[int, dict[str, int]]:
        """Batch root snapshots; native receivers retain collector cascade capture."""

        history = model_history(self.model)
        if history is not None and history.cascade_delete_history:
            history = None
        if history is None or self.query.is_sliced:
            return super().delete()
        self._for_write = True
        selection = self._system_capture(reason="angee.history.rows") if isinstance(self, RebacQuerySet) else self
        with transaction.atomic(using=self.db):
            keys = list(dict.fromkeys(statement_keys(lock_if_supported(selection))))
            if not keys:
                return 0, {}
            current_changeset(required=True, record=True)
            with transaction.atomic(using=self.db, savepoint=False), write_group():
                history.bulk_history_create(self.model, keys, "-", using=self.db)
                counts: dict[str, int] = {}
                for rows in by_key(self, keys):
                    _, deleted = super(_AngeeQuerySetMixin, rows).delete()
                    for label, count in deleted.items():
                        counts[label] = counts.get(label, 0) + count
                self._result_cache = None
                return sum(counts.values()), counts

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


class RetainedReferenceQuerySet(_AngeeQuerySetMixin[_ModelT], TrackedQuerySet[_ModelT]):
    """Django's scheduled retained-FK updates, with capture and system authority."""

    def update(self, **kwargs: Any) -> int:
        """Remove the edge as part of its authorized target deletion."""

        with system_context(reason="delete.retain"):
            return super().update(**kwargs)


class AngeeUnscopedQuerySet(
    _AngeeQuerySetMixin[_ModelT],
    TrackedQuerySet[_ModelT],
):
    """Angee queryset API for intentionally permission-naive managers.

    Used by models without REBAC row policy and explicit Django base managers
    whose unfiltered relation reads must retain native Django semantics. It
    composes the library's tracked writes required by a declared base manager.
    """

    def scoped_for_aggregate(self) -> Self:
        """Return this queryset for permission-naive aggregation.

        The manager deliberately supplies no row authorization; aggregation
        preserves that same explicit unscoped policy.
        """

        return self


class StatementQuerySet(AngeeUnscopedQuerySet[Any]):
    """Chainable assertion scopes; the manager owns subject admission."""

    def current(self) -> Any:
        return self.filter(superseded__isnull=True)

    def canonical(self) -> Any:
        return self.current().filter(
            status=self.model.StatementStatus.ASSERTED, applied=True, snak=self.model.StatementSnak.VALUE
        )

    def provenance(self, field: str) -> Any:
        """Return the applied assertions, including a preferred dismissal."""

        aspects = [
            name for name, aspect in self.model.statement_aspects.items() if field in (*aspect.fields, *aspect.members)
        ]
        if not aspects:
            raise FieldDoesNotExist(f"No aspect owns {field!r}.")
        return self.current().filter(aspect__in=aspects, applied=True)

    def delete(self) -> Any:
        raise ValidationError("Statements are retained; withdraw them instead.")
