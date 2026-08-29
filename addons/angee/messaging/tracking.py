"""Odoo-style field-change tracking for record chatter.

``ThreadedModelMixin`` logs configured field changes into a record's chatter on save.
This module owns the generic field-diff mechanism it composes: :class:`FieldTracker`
reads the configured tracked fields, snapshots their pre-save values, diffs them
against the post-save values, and renders human displays, emitting :class:`TrackingChange`
rows. Keeping the ~130-line mechanism here (instead of on the mixin) keeps it out of
every consumer model's MRO and lets the mixin stay the thin, permission-gated verb owner.

:class:`TrackingChange` is the tracked old→new row shape authored once, so the mixin's
tracker builds it and the message write path persists it from the same shape — the row
shape is not declared in the mixin and re-validated again in the manager.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django.utils.text import capfirst


@dataclass(frozen=True)
class TrackingChange:
    """One tracked field's old→new values, in ``TrackingValue``'s row shape.

    Emitted by :class:`FieldTracker` and consumed by the message write path
    (``MessageManager.post_to_thread`` via ``_normalise_tracking_value``), so the tracked
    field set is declared in exactly one place.
    """

    field_name: str
    field_label: str
    field_type: str
    old_value: Any
    new_value: Any
    old_display: str
    new_display: str


class FieldTracker:
    """The field-diff mechanism ``ThreadedModelMixin`` composes to track record changes.

    Bound to one record instance and its configured ``thread_tracking_fields``; the mixin
    delegates snapshotting/diffing/rendering here and keeps only the chatter verbs.
    """

    def __init__(self, instance: models.Model, field_names: tuple[str, ...]) -> None:
        self._instance = instance
        self._field_names = field_names

    def snapshot(self, update_fields: Iterable[str] | None = None) -> tuple[dict[str, Any], ...]:
        """Return the pre-save old values for the tracked fields, before ``save``."""

        instance = self._instance
        if instance._state.adding or instance.pk is None:
            return ()
        fields = self._fields(update_fields)
        if not fields:
            return ()
        row = (
            type(instance)
            ._base_manager.filter(pk=instance.pk)
            .values(*(field.attname for field in fields))
            .first()
        )
        if row is None:
            return ()
        return tuple(
            {
                "field": field,
                "old_value": row[field.attname],
                "old_display": self._display(field, row[field.attname]),
            }
            for field in fields
        )

    def changes(self, snapshot: tuple[dict[str, Any], ...]) -> tuple[TrackingChange, ...]:
        """Return the tracked fields whose value changed since ``snapshot``."""

        changes: list[TrackingChange] = []
        for item in snapshot:
            field = cast(models.Field, item["field"])
            old_value = item["old_value"]
            new_value = getattr(self._instance, field.attname)
            if old_value == new_value:
                continue
            changes.append(
                TrackingChange(
                    field_name=field.name,
                    field_label=capfirst(str(field.verbose_name or field.name)),
                    field_type=field.get_internal_type(),
                    old_value=old_value,
                    new_value=new_value,
                    old_display=str(item["old_display"]),
                    new_display=self._display(field, new_value),
                )
            )
        return tuple(changes)

    def create_changes(self) -> tuple[TrackingChange, ...]:
        """Return the tracked initial (non-default) values for the record's first save."""

        changes: list[TrackingChange] = []
        for field in self._fields(None):
            new_value = getattr(self._instance, field.attname)
            if not new_value:
                continue
            if field.has_default() and new_value == field.get_default():
                continue
            changes.append(
                TrackingChange(
                    field_name=field.name,
                    field_label=capfirst(str(field.verbose_name or field.name)),
                    field_type=field.get_internal_type(),
                    old_value=None,
                    new_value=new_value,
                    old_display="",
                    new_display=self._display(field, new_value),
                )
            )
        return tuple(changes)

    def _fields(self, update_fields: Iterable[str] | None) -> tuple[models.Field[Any, Any], ...]:
        """Return the configured concrete fields considered for tracking on this save."""

        if not self._field_names:
            return ()
        cls = type(self._instance)
        wanted = set(update_fields) if update_fields is not None else None
        fields: list[models.Field[Any, Any]] = []
        for name in self._field_names:
            try:
                field = cls._meta.get_field(name)
            except FieldDoesNotExist as error:
                raise ImproperlyConfigured(
                    f"{cls._meta.label}.thread_tracking_fields includes unknown field {name!r}."
                ) from error
            if not isinstance(field, models.Field) or not field.concrete or getattr(field, "many_to_many", False):
                raise ImproperlyConfigured(
                    f"{cls._meta.label}.thread_tracking_fields can only include concrete model fields."
                )
            if wanted is not None and field.name not in wanted and field.attname not in wanted:
                continue
            fields.append(field)
        return tuple(fields)

    def _display(self, field: models.Field[Any, Any], value: Any) -> str:
        """Return the human display value for one tracked field value."""

        if value in (None, ""):
            return ""
        if field.choices:
            return str(dict(field.flatchoices).get(value, value))
        if isinstance(field, models.ForeignKey):
            related = field.remote_field.model._base_manager.filter(pk=value).first()
            return str(related) if related is not None else str(value)
        return str(value)
