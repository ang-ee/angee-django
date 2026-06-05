"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from typing import Any, ClassVar

import reversion
from django.conf import settings
from django.db import models
from django_sqids import SqidsField
from rebac import app_settings, current_actor


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


class AuditMixin(models.Model):
    """Add conventional user-owned audit foreign keys to a model."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    """The user that created the row, when known."""

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    """The user that most recently updated the row, when known."""

    class Meta:
        """Django model options for audit-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row after stamping user audit fields."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                super().save(*args, **kwargs)
                return

        actor = getattr(self, "_rebac_actor", None) or current_actor()
        touched: set[str] = set()
        if actor is not None and actor.subject_type == app_settings.REBAC_USER_TYPE:
            if self._state.adding:
                if getattr(self, "created_by_id", None) is None:
                    self.created_by_id = actor.subject_id
                    touched.add("created_by")
                if getattr(self, "updated_by_id", None) is None:
                    self.updated_by_id = actor.subject_id
                    touched.add("updated_by")
            else:
                self.updated_by_id = actor.subject_id
                touched.add("updated_by")

        if touched and update_fields is not None:
            kwargs["update_fields"] = update_fields | touched
        super().save(*args, **kwargs)


class HistoryMixin(models.Model):
    """Mark a model as tracked by django-simple-history."""

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

    @property
    def revisions(self) -> Any:
        """Return this row's django-reversion versions newest-first."""

        versions = reversion.models.Version.objects.get_for_object(self)
        return versions.select_related("revision")

    def revert_to(self, version: Any) -> None:
        """Restore declared revisioned fields from ``version`` and save."""

        data = version.field_dict
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
        self.save()
