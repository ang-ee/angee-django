"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import DatabaseError, models, transaction
from django.db.models import F
from rebac import (
    system_context,
)
from rebac.actors import is_sudo

from angee.base.exceptions import DomainError
from angee.base.models.persistence import PersistenceMixin


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


class RowLockMixin(models.Model):
    """Lock a row's system-owned fields against user writes.

    :meth:`locked_fields` names the fields a row's own loaded state locks; a row
    with any locked field is a system row. A user write may change only its
    unlocked fields: it cannot change a locked field, delete the row, or turn a
    new or unlocked row into a system row. System writers (provisioning and
    resource loads under ``system_context``) own system rows and are not
    checked. An elevation that only authorizes a user's write still checks:
    editable lines write their children elevated under the parent's write
    permission, so :class:`~angee.graphql.data.hasura.HasuraLines` calls
    :meth:`validate_row_lock` and :meth:`validate_row_delete` itself before each
    child write. Child nodes project the same fact as ``locked_fields``.
    """

    locked_rows_label: ClassVar[str | None] = None
    """Plural phrase naming the system rows in refusals; defaults to ``System <plural>``."""

    class Meta:
        """Django options for the shared system-row lock."""

        abstract = True

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
            if self.pk is not None and not self._state.adding:
                with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                    persisted = type(self)._base_manager.filter(pk=self.pk).first()
            self.validate_row_lock(None if persisted is None else persisted.row_lock())
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Refuse a user deleting a committed system row."""

        if not is_sudo():
            with system_context(reason=f"{self._meta.label_lower}.row_lock"):
                persisted = type(self)._base_manager.filter(pk=self.pk).first()
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
        using = manager.db
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
