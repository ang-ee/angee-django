"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import reversion
from django.conf import settings
from django.db import models
from rebac import app_settings, current_actor

from angee.base.fields import SqidField


def actor_user_id(actor: Any) -> Any | None:
    """Return ``actor``'s user subject id, or ``None`` when it is not a user.

    The one reading of "this REBAC actor, as a user id" — the value that backs
    user-owned columns (``created_by`` / ``updated_by`` / ``trashed_by`` …).
    """

    if actor is not None and actor.subject_type == app_settings.REBAC_USER_TYPE and actor.subject_id:
        return actor.subject_id
    return None


@dataclass(frozen=True, slots=True)
class ModelDecorator:
    """Decorator the composer applies to emitted concrete models."""

    import_path: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()
    kwargs_from_model: tuple[tuple[str, str], ...] = ()
    enabled_by_model_attr: str = ""


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
    """Add an opaque public identifier backed by the model primary key.

    A model sets only the varying fact — its prefix — as ``sqid_prefix``
    (e.g. ``sqid_prefix = "nte_"``); the shared ``sqid`` column reads it (see
    ``SqidField.contribute_to_class``), so no model re-declares the field.
    """

    sqid_prefix: ClassVar[str] = ""
    """Public-id prefix for ``sqid`` (e.g. ``"nte_"``); empty means no prefix."""

    sqid = SqidField(real_field_name="id", min_length=8)
    """Opaque public identifier encoded from the integer primary key."""

    class Meta:
        """Django model options for sqid-only abstract inheritance."""

        abstract = True

    def public_id_value(self) -> Any:
        """Return the raw public identifier value for this instance."""

        return self.sqid

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        return {"sqid": value}

    @classmethod
    def public_id_from_pk(cls, value: Any) -> str:
        """Return the public id encoded from this model's primary-key value."""

        field = cls._meta.get_field("sqid")
        if not isinstance(field, SqidField):
            return ""
        return field.public_id_from_value(value)


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

        user_id = actor_user_id(getattr(self, "_rebac_actor", None) or current_actor())
        touched: set[str] = set()
        if user_id is not None:
            if self._state.adding:
                if getattr(self, "created_by_id", None) is None:
                    self.created_by_id = user_id
                    touched.add("created_by")
                if getattr(self, "updated_by_id", None) is None:
                    self.updated_by_id = user_id
                    touched.add("updated_by")
            else:
                self.updated_by_id = user_id
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

    angee_model_decorators: ClassVar[tuple[ModelDecorator, ...]] = (
        ModelDecorator(
            import_path="reversion.register",
            kwargs_from_model=(("fields", "revisioned_fields"),),
            enabled_by_model_attr="revisioned_fields",
        ),
    )
    """Composer decorators applied to emitted concrete revision models."""

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
        """Restore declared revisioned fields from ``version`` and save.

        Saves with ``update_fields`` so unrelated in-memory columns are not
        flushed, but includes the model's ``auto_now`` timestamps so a revert
        advances ``updated_at`` consistently with the audit ``updated_by`` stamp
        (Django only refreshes ``auto_now`` fields named in ``update_fields``).
        """

        data = version.field_dict
        reverted: list[str] = []
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
                reverted.append(name)
        if not reverted:
            return
        auto_now = [field.name for field in self._meta.fields if getattr(field, "auto_now", False)]
        self.save(update_fields=[*reverted, *auto_now])
