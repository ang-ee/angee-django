"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, ClassVar, Self, TypeVar

from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from rebac.managers import RebacQuerySet, TrackedQuerySet

from angee.base.querysets import ClosedUpdateQuerySet, _AngeeQuerySetMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)


class _AppendOnlyWritesMixin(ClosedUpdateQuerySet[_ModelT]):
    """The closed collection-write policy shared by the scoped and base querysets.

    ``owner_update`` and ``owner_bulk_create`` are public, framework-protected
    APIs for domain owners that have already validated fields and predicates.
    They skip this class's guard only, preserving every downstream guard.
    """

    def validate_insert(self) -> None:
        """Let the domain owner narrow insert admission."""

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Iterable[str] | None = None,
        unique_fields: Iterable[str] | None = None,
    ) -> list[_ModelT]:
        """Reject conflict handling before asking the owner to admit inserts."""

        if ignore_conflicts or update_conflicts:
            raise self.immutable_error("bulk_create")
        self.validate_insert()
        return self.owner_bulk_create(objs, batch_size=batch_size)

    def owner_bulk_create(self, objs: Iterable[_ModelT], *, batch_size: int | None = None) -> list[_ModelT]:
        """Protected API: insert an owner-validated batch through downstream guards."""

        return super().bulk_create(objs, batch_size=batch_size)

    def delete(self) -> tuple[int, dict[str, int]]:
        """Reject deletion through the collection."""

        raise self.immutable_error("delete")

    def _raw_delete(self, using: str | None) -> int:
        """Reject direct SQL deletion through the queryset."""

        raise self.immutable_error("_raw_delete")


class AppendOnlyQuerySet(_AppendOnlyWritesMixin[_ModelT], RebacQuerySet[_ModelT]):
    """Close generic collection edits and deletion around owner-controlled writes.

    Compose before the domain's base queryset to preserve authorization.
    Retained evidence uses insert admission; retained state machines expose
    exact conditional writes through their own methods and ``owner_update``.
    Instance invariants and collector retention remain model/FK concerns;
    ``AuditMixin`` clears audit FKs through its collector policy without
    calling this queryset.
    """

    def insert(self, obj: _ModelT) -> _ModelT:
        """Validate one append before its ordinary authorized insertion."""

        self.validate_insert()
        return super().insert(obj)


def _validate_model_insert(model: type[models.Model]) -> None:
    """Delegate every insert path to the default queryset's admission policy."""

    rows = model._default_manager.get_queryset()
    if not isinstance(rows, AppendOnlyQuerySet):
        raise ImproperlyConfigured(f"{model._meta.label}'s default manager must compose AppendOnlyQuerySet.")
    rows.validate_insert()


class AppendOnlyBaseQuerySet(
    _AppendOnlyWritesMixin[_ModelT],
    _AngeeQuerySetMixin[_ModelT],
    TrackedQuerySet[_ModelT],
):
    """Unscoped rows for Django's own relation reads, with the same writes closed."""

    def validate_insert(self) -> None:
        """Apply the domain's default-queryset admission to base-manager inserts."""

        _validate_model_insert(self.model)


_AppendOnlyBaseManagerBase = models.Manager.from_queryset(AppendOnlyBaseQuerySet)


class AppendOnlyBaseManager(_AppendOnlyBaseManagerBase):
    """The base manager an append-only model declares: unfiltered reads, closed writes."""


class AppendOnlyModel(models.Model):
    """Admit new rows while reserving retained-row changes for owner verbs.

    The model's default manager must compose :class:`AppendOnlyQuerySet`. An owner may
    use ``_owner_insert`` after its own admission or ``_owner_delete`` after its
    own retention check; ordinary instance and collection writes stay closed.
    """

    _angee_base: ClassVar[models.Manager[Self]] = AppendOnlyBaseManager()

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require the default manager to retain the append-only write contract."""

        errors = super().check(**kwargs)
        if not cls._meta.abstract and not isinstance(cls._default_manager.get_queryset(), AppendOnlyQuerySet):
            errors.append(
                checks.Error(
                    f"{cls._meta.label}'s default manager must compose AppendOnlyQuerySet.",
                    hint="Declare a default manager whose queryset inherits AppendOnlyQuerySet.",
                    obj=cls,
                    id="angee.E034",
                )
            )
        return errors

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            raise ValidationError(f"{self._meta.label} rows cannot be edited.")
        _validate_model_insert(type(self))
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        raise ValidationError(f"{self._meta.label} rows cannot be deleted.")

    def _owner_insert(self) -> None:
        """Insert one owner-admitted row, including domains with closed generic admission."""
        if not self._state.adding:
            raise ValidationError(f"{self._meta.label} rows cannot be edited.")
        super().save(force_insert=True)

    def _owner_delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Delete one row after its domain owner has checked retention and locks."""
        return super().delete(*args, **kwargs)
