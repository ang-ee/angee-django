"""Timestamp fields and the native write-stamping contract they declare."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import models


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    """The timestamp when the row was first created."""

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

    return tuple(field.name for field in model._meta.concrete_fields if getattr(field, "auto_now", False))
