"""Parent-owned atomic replacement of owned reverse-FK child sets."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import copy
from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, transaction

from angee.base.fields.ordering import FractionalRankField
from angee.base.models.identity import public_id_of
from angee.base.models.inputs import InputExtensionsMixin
from angee.base.models.locks import RowLockMixin
from angee.base.querysets import row_lock_paths


class OwnedChildrenMixin(models.Model):
    """Persist an owned aggregate through its parent, without owning a wire format."""

    class Meta:
        abstract = True

    def save_children(
        self, field: str, rows: Iterable[tuple[str | None, dict[str, Any], dict[str, Any]]], *,
        create: Callable[[dict[str, Any]], models.Model],
        update: Callable[[models.Model, dict[str, Any], Callable[[models.Model], None] | None], Any],
        position_field: str = "position", replace: bool = True,
    ) -> None:
        """Apply a decoded desired child set while holding this parent's row lock.

        The caller resolves relation identities and supplies its
        native persistence callbacks, which retain relation/input semantics. Row
        membership, removals, rank staging, prospective locks and input-extension
        application belong to this parent. ``replace=False`` is for the owning
        creation verb: it appends to children provisioned during parent creation.
        An enabled access-policy owner authorizes/elevates through a cooperative
        override; this neutral owner has no permission API.
        The caller's transaction encloses parent columns and this child set.
        """

        relation = self._meta.get_field(field)
        if not isinstance(relation, models.ManyToOneRel):
            raise ImproperlyConfigured(f"{self._meta.label}.{field} must be an owned reverse-FK relation.")
        child_model = relation.related_model
        back_fk = relation.field.name
        back_fk_id = relation.field.attname
        prepared = tuple(rows)
        identifiers = [identifier for identifier, _data, _extensions in prepared if identifier is not None]
        if len(identifiers) != len(set(identifiers)):
            raise ValidationError("A child may appear only once in the desired set.")
        # The parent lock serializes desired-set membership and rank staging.
        with transaction.atomic(using=self._state.db):
            type(self)._default_manager.using(self._state.db).select_for_update(
                of=row_lock_paths(type(self)),
            ).filter(pk=self.pk).first()
            children = child_model._base_manager.using(self._state.db).filter(**{back_fk: self})
            existing = children.in_bulk()
            by_public_id = {public_id_of(child): child for child in existing.values()}
            unknown = sorted(
                pid for pid, _data, _extensions in prepared if pid is not None and pid not in by_public_id
            )
            if unknown:
                raise ValidationError(
                    f"{child_model._meta.object_name} lines {unknown!r} are not part of "
                    f"{self._meta.object_name} {public_id_of(self)!r}; reload and retry."
                )
            kept_pks = {
                by_public_id[public_id].pk for public_id, _data, _extensions in prepared if public_id is not None
            }
            removed = sorted(set(existing) - kept_pks) if replace else []
            for pk in removed:
                child = existing[pk]
                if isinstance(child, RowLockMixin):
                    child.validate_row_delete()
            if removed:
                # Removals free their unique values (a name) for the rows that follow.
                try:
                    children.filter(pk__in=removed).delete()
                except (models.ProtectedError, models.RestrictedError) as error:
                    raise ValidationError(_line_removal_refusal(child_model, error)) from error
            try:
                position = child_model._meta.get_field(position_field)
            except FieldDoesNotExist:
                position = None
            if isinstance(position, FractionalRankField):
                moving = [
                    by_public_id[public_id]
                    for public_id, decoded, _extensions in prepared
                    if public_id is not None and decoded.get(position.name) is not None
                    and position.to_python(decoded[position.name]) != getattr(by_public_id[public_id], position.attname)
                ]
                temporary = position.get_temporary_ranks(
                    moving, (decoded.get(position.name) for _public_id, decoded, _extensions in prepared),
                    using=children.db,
                )
                if moving:
                    # Immediate uniqueness forbids cyclic swaps. Stage through
                    # the native guarded/captured queryset, then save exact final
                    # values through the unchanged model resolver below.
                    staged = [copy(child) for child in moving]
                    for child, rank in zip(staged, temporary, strict=True):
                        setattr(child, position.attname, rank)
                    children.bulk_update(staged, [position.name])
            for public_id, decoded, extensions in prepared:
                if public_id is not None:
                    child = by_public_id[public_id]
                    validate = _row_lock_check(child.row_lock()) if isinstance(child, RowLockMixin) else None
                    update(child, decoded, validate)
                else:
                    values = {**decoded, back_fk_id: self.pk}
                    if issubclass(child_model, RowLockMixin):
                        _unsaved_row(child_model, values).validate_row_lock(None)
                    # Created through the manager, so its factory invariants still apply.
                    child = create(values)
                if extensions:
                    if not isinstance(child, InputExtensionsMixin):
                        raise ImproperlyConfigured(f"{child_model._meta.label} does not support input extensions.")
                    child.apply_input_extensions(**extensions)


def _line_removal_refusal(
    child_model: type[models.Model],
    error: models.ProtectedError | models.RestrictedError,
) -> str:
    """Say which kinds of record still use removed lines, counted, never naming the rows."""

    blockers = error.protected_objects if isinstance(error, models.ProtectedError) else error.restricted_objects
    counts: dict[str, int] = {}
    for row in blockers:
        name = str(type(row)._meta.verbose_name_plural)
        counts[name] = counts.get(name, 0) + 1
    used_by = ", ".join(f"{count} {name}" for name, count in sorted(counts.items()))
    return (
        f"Some {child_model._meta.verbose_name_plural} cannot be removed: other records use them "
        f"({used_by}). Keep them, or remove what uses them first."
    )


def _row_lock_check(previous: Mapping[str, Any]) -> Callable[[models.Model], None]:
    """Return the pre-save check of an elevated line update against its persisted lock."""

    def validate(instance: models.Model) -> None:
        if not isinstance(instance, RowLockMixin):
            raise ImproperlyConfigured(f"{instance._meta.label} does not support row locks.")
        instance.validate_row_lock(previous)

    return validate


def _unsaved_row[M: models.Model](model: type[M], values: Mapping[str, Any]) -> M:
    """Return an unsaved row carrying a new line's concrete column values, for its lock check."""

    columns = {name for field in model._meta.concrete_fields for name in (field.name, field.attname)}
    return model(**{name: value for name, value in values.items() if name in columns})
