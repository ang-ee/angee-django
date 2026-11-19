"""Timestamp fields and the native write-stamping contract they declare."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import models
from django.db.models.expressions import Col
from django.utils import timezone
from django.utils.functional import cached_property


class CreatedAtField(models.DateTimeField):
    """When a row came into being: Django's ``auto_now_add`` stamp, which a creating owner may state at insert.

    An insert keeps a time already on the instance, a future one clamped to the
    insert, so an owner that creates a row from a source's record states when the
    record came into being by constructing the row with ``created_at=``; an insert
    with none stated stamps now, as ``auto_now_add`` does. A save of an existing row
    stamps nothing. History's ``+`` snapshot reads the stored row, so it carries the
    stated time, while its ``history_date`` stays the moment the row was recorded.

    Who may state a time: a creating owner, for a row no reader treats as having
    arrived here when it was stamped. One reader does today: an events send guard
    refuses a message due before its registration or order existed, so a
    registration or an order states none until its owner reads an arrival stamp of
    its own; an owner that then restates the stamp of such a row sets the arrival
    stamp from the old ``created_at`` first, in the same statement. Handles,
    statements and record links keep the moment they were recorded. A stamp that
    starts, or an owner's writer later moves, into the past never enters a
    "created since" window or a cursor ordered by it (``created_at__gt``, a keyset
    page over it): a reader that means "arrived here since" reads an arrival fact,
    never this stamp.

    It deconstructs as ``django.db.models.DateTimeField``: it adds no persisted
    option, so a host's migration state and historical models hold the field Django
    declares, and no host generates a migration for it. Its column is typed as that
    field too (:attr:`value_field`, as Django's ``GeneratedField`` types its column by
    its ``output_field``), so an expression combining it with any other
    ``DateTimeField`` (``Coalesce("sent_at", "created_at")``) resolves its type as
    before.
    """

    @cached_property
    def value_field(self) -> models.DateTimeField:
        """The field Django declares, which types this column in every expression."""

        return models.DateTimeField()

    @cached_property
    def cached_col(self) -> Col:
        return Col(self.model._meta.db_table, self, self.value_field)

    def get_col(self, alias: str, output_field: Any = None) -> Col:
        if output_field is None or output_field == self:
            if alias == self.model._meta.db_table:
                return self.cached_col
            output_field = self.value_field
        return super().get_col(alias, output_field)

    def pre_save(self, model_instance: models.Model, add: bool) -> Any:
        stated = getattr(model_instance, self.attname)
        if add and self.auto_now_add and stated is not None:
            value = min(stated, timezone.now())
            setattr(model_instance, self.attname, value)
            return value
        return super().pre_save(model_instance, add)

    def deconstruct(self) -> Any:
        name, _path, args, kwargs = super().deconstruct()
        return name, "django.db.models.DateTimeField", args, kwargs


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = CreatedAtField(auto_now_add=True, db_index=True)
    """When the row came into being: its insert, or the earlier time its creator stated (:class:`CreatedAtField`)."""

    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    """The timestamp when the row was most recently saved."""

    class Meta:
        """Django model options for timestamp-only abstract inheritance."""

        abstract = True


def update_fields_with_auto_now(instance: models.Model, update_fields: Any) -> set[str]:
    """Return non-empty ``update_fields`` plus this model's ``auto_now`` fields."""

    fields = set(update_fields)
    if not fields:
        return fields
    return fields | set(_auto_now_fields(type(instance)))


def auto_now_stamps(model: type[models.Model], now: datetime) -> dict[str, Any]:
    """Return the ``auto_now`` columns a queryset update of ``model`` must stamp itself."""

    return dict.fromkeys(_auto_now_fields(model), now)


def _auto_now_fields(model: type[models.Model]) -> tuple[str, ...]:
    """Return the names of ``model``'s ``auto_now`` fields, which every write stamps."""

    # Django temporal fields declare auto_now; other Field subclasses do not.
    return tuple(field.name for field in model._meta.concrete_fields if getattr(field, "auto_now", False))
