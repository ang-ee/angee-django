"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from typing import ClassVar

from django.db import models
from django_sqids import SqidsField
from simple_history.models import HistoricalRecords


class TimestampMixin(models.Model):
    """Add conventional creation and update timestamps to a model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    """The timestamp when the row was first created."""

    updated_at = models.DateTimeField(auto_now=True, db_index=True)
    """The timestamp when the row was most recently saved."""

    class Meta:
        """Django model options for timestamp-only abstract inheritance."""

        abstract = True


class SqidMixin(models.Model):
    """Add an opaque public identifier backed by the model primary key."""

    sqid = SqidsField(real_field_name="id")
    """Opaque public identifier encoded from the integer primary key."""

    class Meta:
        """Django model options for sqid-only abstract inheritance."""

        abstract = True


class HistoryMixin(models.Model):
    """Mark a model as tracked by django-simple-history."""

    history = HistoricalRecords(inherit=True)
    """Historical row manager supplied by django-simple-history."""

    class Meta:
        """Django model options for history-only abstract inheritance."""

        abstract = True


class RevisionMixin(models.Model):
    """Mark a model as tracked by django-reversion snapshots."""

    revisioned_fields: ClassVar[tuple[str, ...]] = ()
    """Model field names registered with django-reversion."""

    class Meta:
        """Django model options for revision-only abstract inheritance."""

        abstract = True
