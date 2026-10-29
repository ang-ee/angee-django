"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from copy import copy
from typing import Any, ClassVar, Self, TypeVar, cast

from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, models, router, transaction
from django.db.models import F
from rebac import (
    system_context,
)
from rebac.actors import is_sudo
from rebac.managers import RebacQuerySet

from angee.base.exceptions import DomainError
from angee.base.models.persistence import PersistenceMixin
from angee.base.querysets import AngeeUnscopedQuerySet, by_key


class ImmutableFieldsMixin(models.Model):
    """Reject changes to loaded immutable attnames declared anywhere in the MRO.

    Donors contribute plain tuples. An owning verb permits its next write through
    ``allow_immutable_save``; unloaded deferred values remain untouched.
    """

    immutable_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        """Django options for the shared write-once field guard."""

        abstract = True

    def allow_immutable_save(self, *field_names: str) -> None:
        """Allow the next save to change named immutable attnames."""

        allowed = set(getattr(self, "_allowed_immutable_fields", set()))
        allowed.update(field_names)
        self._allowed_immutable_fields = allowed

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist after comparing immutable facts with the committed row."""

        allowed = set(getattr(self, "_allowed_immutable_fields", set()))
        try:
            if self.pk is not None and not self._state.adding:
                declared = dict.fromkeys(
                    name for base in type(self).__mro__ for name in base.__dict__.get("immutable_fields", ())
                )
                checked = tuple(name for name in declared if name not in allowed and name in self.__dict__)
                if checked:
                    # Compare committed identities without loading unrelated deferred columns.
                    with system_context(reason=f"{self._meta.label_lower}.immutable_fields"):
                        persisted = type(self)._base_manager.filter(pk=self.pk).values(*checked).first()
                    if persisted is not None:
                        changed = [name for name in checked if persisted[name] != getattr(self, name)]
                        if changed:
                            raise ValidationError(
                                {name.removesuffix("_id"): "This identity or receipt is immutable." for name in changed}
                            )
            super().save(*args, **kwargs)
        finally:
            self._allowed_immutable_fields = set()


_ModelT = TypeVar("_ModelT", bound=models.Model)


class _RowLockWritesQuerySet(models.QuerySet[_ModelT]):
    """Apply the row owner's invariant to native collection persistence."""

    def _lock_bypassed(self) -> bool:
        return self.is_sudo() if isinstance(self, RebacQuerySet) else is_sudo()

    def update(self, **kwargs: Any) -> int:
        """Validate prospective values, including native SQL expressions, under row locks."""

        if self._lock_bypassed() or not kwargs:
            return super().update(**kwargs)
        rows = self.select_for_update()
        alias = rows.db
        with transaction.atomic(using=alias):
            rows = rows.with_action("write").scoped() if isinstance(rows, RebacQuerySet) else rows
            fields: dict[str, models.Field[Any, Any]] = {}
            for name in kwargs:
                field = self.model._meta.get_field(name)
                if not isinstance(field, models.Field):
                    raise ValidationError({name: "Collection writes require a stored field."})
                fields[name] = field
            output_fields = {
                name: field.target_field if isinstance(field, models.ForeignKey) else field
                for name, field in fields.items()
            }
            projections = {
                f"_row_lock_value_{index}": (
                    value if isinstance(value, (models.expressions.BaseExpression, models.F)) else models.Value(
                        value.pk if isinstance(value, models.Model) else value,
                        output_field=output_fields[name],
                    )
                )
                for index, (name, value) in enumerate(kwargs.items())
            }
            validated = []
            for previous in rows.annotate(**projections):
                candidate = copy(previous)
                for index, field in enumerate(fields.values()):
                    setattr(candidate, field.attname, getattr(previous, f"_row_lock_value_{index}"))
                cast(RowLockMixin, candidate).validate_row_lock(cast(RowLockMixin, previous).row_lock())
                validated.append(previous.pk)
            # READ COMMITTED may admit new predicate matches after validation.
            # Only the locked, checked identities may reach downstream writers.
            written = sum(
                super(_RowLockWritesQuerySet, batch).update(**kwargs)
                for batch in by_key(self.using(alias), validated)
            )
            self._result_cache = None
            return written

    def bulk_create(
        self,
        objs: Iterable[_ModelT],
        batch_size: int | None = None,
        ignore_conflicts: bool = False,
        update_conflicts: bool = False,
        update_fields: Collection[str] | None = None,
        unique_fields: Collection[str] | None = None,
    ) -> list[_ModelT]:
        """Admit new system-row state through the same instance invariant."""

        candidates = list(objs)
        if not self._lock_bypassed():
            if update_conflicts:
                raise ValidationError("Row-locked conflict updates require checked instance saves.")
            for row in candidates:
                cast(RowLockMixin, row).validate_row_lock(None)
        return super().bulk_create(
            candidates, batch_size, ignore_conflicts, update_conflicts, update_fields, unique_fields,
        )


class RowLockQuerySet(_RowLockWritesQuerySet[_ModelT], RebacQuerySet[_ModelT]):
    """Compose before an Angee queryset for models with system-owned rows."""


class _RowLockBaseQuerySet(_RowLockWritesQuerySet[_ModelT], AngeeUnscopedQuerySet[_ModelT]):
    """Keep the same invariant on native unfiltered manager writes."""


_RowLockBaseManager = models.Manager.from_queryset(_RowLockBaseQuerySet)


def refuse_locked_row_delete(sender: Any, instance: RowLockMixin, origin: Any, **kwargs: Any) -> None:
    """Apply the row's declared direct/cascade delete policy in Django's collector."""

    del sender, kwargs
    elevated = origin.is_sudo() if isinstance(origin, RebacQuerySet) else is_sudo()
    if elevated:
        return
    if instance.row_lock_cascade_parent:
        field = instance._meta.get_field(instance.row_lock_cascade_parent)
        if not isinstance(field, models.ForeignKey) or field.related_model is None:
            raise ImproperlyConfigured("The row-lock cascade owner must be a forward foreign key.")
        parent = field.related_model
        parent_pk = field.value_from_object(instance)
        if isinstance(origin, parent) and origin.pk == parent_pk:
            return
        if isinstance(origin, models.QuerySet) and origin.model is parent and origin.filter(pk=parent_pk).exists():
            return
    instance.validate_row_delete()


class RowLockMixin(models.Model):
    """Lock a row's system-owned fields against user writes.

    :meth:`locked_fields` names the fields a row's own loaded state locks; a row
    with any locked field is a system row. A user write may change only its
    unlocked fields: it cannot change a locked field, delete the row, or turn a
    new or unlocked row into a system row. System writers (provisioning and
    resource loads under ``system_context``) own system rows and are not
    checked. The default manager composes ``RowLockQuerySet``; the base manager
    and native collector retain the same invariant. An elevation that only
    authorizes a user's write still checks:
    editable lines write their children elevated under the parent's write
    permission, so :class:`~angee.graphql.data.hasura.HasuraLines` calls
    :meth:`validate_row_lock` and :meth:`validate_row_delete` itself before each
    child write. Child nodes project the same fact as ``locked_fields``.
    """

    _angee_base: ClassVar[models.Manager[Self]] = _RowLockBaseManager()
    row_lock_cascade_parent: ClassVar[str | None] = None
    """An owning parent whose direct collector deletion may remove system children."""

    locked_rows_label: ClassVar[str | None] = None
    """Plural phrase naming the system rows in refusals; defaults to ``System <plural>``."""

    class Meta:
        """Django options for the shared system-row lock."""

        abstract = True

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require all model collection writers to preserve the system-row invariant."""

        errors = super().check(**kwargs)
        if not cls._meta.abstract and not isinstance(cls._default_manager.get_queryset(), RowLockQuerySet):
            errors.append(checks.Error(
                f"{cls._meta.label}'s default manager must compose RowLockQuerySet.",
                obj=cls, id="angee.E048",
            ))
        if not cls._meta.abstract and not isinstance(cls._base_manager.get_queryset(), _RowLockWritesQuerySet):
            errors.append(checks.Error(
                f"{cls._meta.label}'s base manager must preserve row-lock collection guards.",
                obj=cls, id="angee.E049",
            ))
        return errors

    def locked_fields(self) -> tuple[str, ...]:
        """Return the fields this row's loaded state locks; empty when the row is unlocked."""

        return ()

    def row_lock(self) -> dict[str, Any]:
        """Return this row's locked fields with their current values."""

        return {name: self._lock_field(name).value_from_object(self) for name in self.locked_fields()}

    def _lock_field(self, name: str) -> models.Field[Any, Any]:
        field = self._meta.get_field(name)
        if not isinstance(field, models.Field):
            raise ImproperlyConfigured(f"{self._meta.label}.{name} must be a stored field to be locked.")
        return field

    def validate_row_lock(self, previous: Mapping[str, Any] | None) -> None:
        """Refuse this state as a user write over ``previous``, the row's persisted :meth:`row_lock`.

        ``previous`` is ``None`` (or empty) for a new or unlocked row, which a
        user write must not turn into a system row.
        """

        label = self._locked_rows_label()
        if previous:
            changed = {}
            for name, value in previous.items():
                field = self._lock_field(name)
                if field.value_from_object(self) != value:
                    changed[name] = f"The {field.verbose_name} of {label.lower()} cannot change."
            if changed:
                raise ValidationError(changed)
            return
        if locked := self.locked_fields():
            raise ValidationError(dict.fromkeys(locked, f"{label} are system-provisioned."))

    def validate_row_delete(self) -> None:
        """Refuse deleting this system row as a user write."""

        if self.locked_fields():
            raise ValidationError(f"{self._locked_rows_label()} cannot be deleted.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Check a user write against the committed row's lock before persisting it."""

        if not is_sudo():
            persisted = None
            using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
            if self.pk is not None and not self._state.adding:
                with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                    persisted = type(self)._base_manager.using(using).filter(pk=self.pk).first()
            self.validate_row_lock(None if persisted is None else persisted.row_lock())
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse a user deleting a committed system row."""

        if not is_sudo():
            with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
                persisted = type(self)._base_manager.using(using).filter(pk=self.pk).first()
            (persisted or self).validate_row_delete()
        return cast(tuple[int, dict[str, int]], super().delete(*args, **kwargs))

    def _locked_rows_label(self) -> str:
        """Return the plural phrase naming this model's system rows."""

        return self.locked_rows_label or f"System {self._meta.verbose_name_plural}"



class StaleRevisionError(DomainError):
    """An update expected a revision that is no longer committed.

    ``current`` is ``None`` when the row no longer exists.
    """

    code = "STALE_REVISION"

    def __init__(self, expected: int | None, current: int | None) -> None:
        self.expected = expected
        self.current = current
        Exception.__init__(self, f"Expected revision {expected}; current revision is {current}.")


def validate_revision(value: Any, *, minimum: int = 1) -> None:
    """Require a portable integer revision at or above its owner's minimum."""

    if type(value) is not int or not minimum <= value <= 2**31 - 1:
        raise ValidationError(
            {"expected_revision": f"Expected revision must be an integer from {minimum} to 2147483647."}
        )


def require_revision(*, expected: Any, current: int | None, minimum: int = 1) -> None:
    """Validate the owner's revision bounds and reject a stale expectation."""

    validate_revision(expected, minimum=minimum)
    if expected != current:
        raise StaleRevisionError(expected, current)


class OptimisticLockMixin(PersistenceMixin, models.Model):
    """Count instance saves and optionally compare the revision in the UPDATE.

    Compose before the model base. Inserts start at one; nonempty updates bump
    through a database expression, including partial and deferred saves. Bulk
    queryset updates deliberately do not bump. A guarded compare-and-swap updates
    only the revision-owning table before Django saves the instance in the same
    transaction, even for multi-table children. New instances require an INSERT;
    loaded rows are never resurrected by ``save()``. On unguarded updates,
    ``pre_save`` receivers see an ``F()`` expression in the counter;
    ``post_save`` receivers and callers see the resulting integer.

    ``REVISION_FIELD`` names the counter. A model whose ``revision`` name is
    taken removes the inherited field with ``revision = None``, declares the
    same counter under another name and sets ``REVISION_FIELD`` to it; its
    GraphQL projection still exposes the counter as ``revision``.
    """

    REVISION_FIELD = "revision"
    """Name of the model field holding the save counter."""

    revision = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        abstract = True

    def require_revision(self, expected: Any) -> None:
        """Check a row already locked by an owning verb."""

        require_revision(expected=expected, current=getattr(self, self.REVISION_FIELD))

    def save(self, *, expected_revision: int | None = None, **kwargs: Any) -> None:
        """Save atomically, rejecting a competing update when an expectation is supplied."""

        if expected_revision is not None:
            validate_revision(expected_revision)
            if self._state.adding:
                raise ValidationError({"expected_revision": "An expected revision requires an existing row."})
        counter = self.REVISION_FIELD
        owner = self._meta.get_field(counter).model
        if not issubclass(owner, PersistenceMixin):
            raise ImproperlyConfigured(f"{owner._meta.label} must own system queries for its revision counter.")
        manager = owner._base_manager.db_manager(hints={"instance": self})
        using = kwargs.get("using") or manager.db
        rows = owner.system_queryset().using(using).filter(pk=self._get_pk_val(owner._meta))
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            kwargs["update_fields"] = update_fields
            if not update_fields:
                if expected_revision is not None:
                    current = rows.values_list(counter, flat=True).first()
                    require_revision(expected=expected_revision, current=current)
                return
        previous = self.__dict__.get(counter, models.DEFERRED)
        existing = not self._state.adding
        kwargs["using"] = using
        if existing:
            if update_fields is not None:
                kwargs["update_fields"] = update_fields | {counter}
            kwargs["force_update"] = True
        else:
            kwargs["force_insert"] = True
        try:
            with transaction.atomic(using=using):
                if expected_revision is not None:
                    updated = rows.filter(**{counter: expected_revision}).update(**{counter: F(counter) + 1})
                    if not updated:
                        current = rows.values_list(counter, flat=True).first()
                        raise StaleRevisionError(expected_revision, current)
                    setattr(self, counter, expected_revision + 1)
                elif existing:
                    setattr(self, counter, F(counter) + 1)
                super().save(**kwargs)
        except Exception as error:
            if previous is models.DEFERRED:
                self.__dict__.pop(counter, None)
            else:
                setattr(self, counter, previous)
            # Django doesn't pass force_update to MTI parents; a missing parent's
            # F-expression INSERT raises ValueError before that INSERT can execute.
            if (
                isinstance(error, (DatabaseError, ValueError))
                and existing
                and expected_revision is None
                and not rows.exists()
            ):
                raise StaleRevisionError(expected=None, current=None) from error
            raise
