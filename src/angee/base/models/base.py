"""Permission-neutral Django model contracts for composed Angee applications."""

from __future__ import annotations

from typing import ClassVar, Self

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django_compose.models import Composable

from angee.base.managers import AngeeManager
from angee.base.models.children import OwnedChildrenMixin
from angee.base.models.identity import PublicIdentity, SqidMixin
from angee.base.models.inputs import InputExtensionsMixin
from angee.base.models.merge_references import MergeContractMixin, references
from angee.base.models.persistence import PersistenceMixin
from angee.base.models.timestamps import TimestampMixin
from angee.base.models.transitions import StateTransitionsMixin
from angee.base.querysets import row_lock_paths


class AngeeModel(
    OwnedChildrenMixin,
    InputExtensionsMixin,
    StateTransitionsMixin,
    MergeContractMixin,
    PublicIdentity,
    TimestampMixin,
    PersistenceMixin,
    Composable,
):
    """Abstract Django model; optional capabilities contribute through composition."""

    objects: ClassVar[models.Manager[Self]] = AngeeManager()
    _angee_base: ClassVar[models.Manager[Self]] = AngeeManager()
    record_display_paths: ClassVar[tuple[str, ...] | None] = None
    """Direct columns and forward relations used by ``record_display_label``.

    ``None`` loads only the key for Django's default ``__str__``, or every
    concrete column for a custom label. Related rows are prefetched separately;
    an unreadable related label falls back to the model name in GraphQL.
    """

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"

    @classmethod
    def check(cls, **kwargs: object) -> list[checks.CheckMessage]:
        """Reject label dependencies that the generic GraphQL hint cannot express."""

        errors = super().check(**kwargs)
        paths = cls.record_display_paths
        if paths is None:
            return errors
        if not isinstance(paths, tuple):
            return [
                *errors,
                checks.Error("record_display_paths must be a tuple of direct field names.", obj=cls, id="angee.E050"),
            ]
        for path in paths:
            if not isinstance(path, str) or "__" in path:
                errors.append(
                    checks.Error(f"record_display_paths requires a direct field: {path!r}.", obj=cls, id="angee.E050")
                )
                continue
            try:
                field = cls._meta.get_field(path)
            except FieldDoesNotExist:
                field = None
            if field is None or not field.concrete or field.many_to_many:
                errors.append(
                    checks.Error(f"record_display_paths names no concrete field: {path!r}.", obj=cls, id="angee.E050")
                )
        return errors

    def retained(self) -> bool:
        """Whether an installed live or historical reference retains this row."""
        return any(reference.retains(self) for reference in references(type(self)))

    def revert_removal(self, *, reason: str = "") -> None:
        """Remove an undone creation unless a live reference still needs it."""
        if any(
            reference.rows(self).using(self._state.db).exists()
            for reference in references(type(self))
            if reference.blocks_target_delete(type(self))
        ):
            raise ValidationError(f"{self._meta.label} {self.pk}: live references prevent removal.")
        self.delete()

    def delete_blocker(self) -> str | None:
        """Return a public-safe deletion refusal, or ``None`` when allowed.

        Overrides require a ``pre_delete`` receiver enforcing the same rule on
        instance, queryset, and cascade deletes; previews only project it.
        """

        return None

    def get_delete_blocker(self) -> str | None:
        """Check one row's deletion refusal for a standalone preview or write."""

        return self.delete_blocker()

    def lock_for_delete(self, *, queryset: models.QuerySet[models.Model] | None = None) -> Self | None:
        """Lock and reload this target for deletion in the model's domain order.

        Call inside a transaction, after any caller-owned permission preflight.
        The default locks only this row (including its concrete parents) through
        ``lock_if_supported``; overrides acquire domain locks before delegating.
        Confirmed-delete callers and ``pre_delete`` receivers must share this hook.
        A supplied ``queryset`` preserves the caller's scope and actor binding;
        receivers omit it to use system rows. Return ``None`` if unavailable.
        """

        if queryset is None:
            return type(self).system_queryset(lock=("self",)).filter(pk=self.pk).first()
        target = queryset.select_for_update(of=row_lock_paths(queryset.model)).filter(pk=self.pk).first()
        if target is not None and not isinstance(target, type(self)):
            raise TypeError("A delete queryset must return rows of its target model.")
        return target

    @property
    def record_display_label(self) -> str:
        """Return the public record label shared by references and presentation."""

        return str(self)


class AngeeDataModel(SqidMixin, AngeeModel):
    """Abstract base for Angee rows that participate in public data contracts."""

    class Meta(AngeeModel.Meta):
        """Django model options for Angee's public data model base."""

        abstract = True


def record_display_label(record: models.Model) -> str:
    """Ask an Angee record for its label, preserving Django's string fallback."""

    return record.record_display_label if isinstance(record, AngeeModel) else str(record)
