"""Reusable abstract model mixins for Angee source models."""

from __future__ import annotations

from typing import Any, ClassVar, cast

import reversion
from django.conf import settings
from django.db import models
from rebac import current_actor

from angee.base.actors import actor_user_id
from angee.base.emission import ModelClassAttribute, ModelDecorator
from angee.base.fields import SqidField


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
    return fields | {field.name for field in instance._meta.fields if getattr(field, "auto_now", False)}


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

        # SqidMixin declares ``sqid = SqidField(...)`` unconditionally, so the column
        # is always a SqidField on any subclass.
        field = cast(SqidField, cls._meta.get_field("sqid"))
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

        actor_getter = getattr(self, "actor", None)
        actor = actor_getter() if callable(actor_getter) else None
        if actor is None:
            actor = current_actor()
        user_id = actor_user_id(actor)
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

        if update_fields is not None:
            kwargs["update_fields"] = update_fields_with_auto_now(self, update_fields | touched)
        super().save(*args, **kwargs)


class HistoryMixin(models.Model):
    """Mark a model as tracked by django-simple-history."""

    @classmethod
    def angee_model_attributes(
        cls,
        *,
        app_label: str,
        model_class: type[models.Model],
        extension_bases: tuple[type[models.Model], ...],
    ) -> tuple[ModelClassAttribute, ...]:
        """Return the simple-history class attribute for a concrete model."""

        kwargs: list[tuple[str, Any]] = [("app", app_label)]
        excluded = cls.angee_history_excluded_fields((*extension_bases, model_class))
        if excluded:
            kwargs.append(("excluded_fields", excluded))
        return (
            ModelClassAttribute(
                name="history",
                import_path="simple_history.models.HistoricalRecords",
                kwargs=tuple(kwargs),
            ),
        )

    @staticmethod
    def angee_history_excluded_fields(
        model_bases: tuple[type[models.Model], ...],
    ) -> list[str]:
        """Return source fields simple-history cannot mirror."""

        excluded: set[str] = set()
        for model_base in model_bases:
            meta = model_base._meta
            own_fields = (
                *meta.local_fields,
                *meta.private_fields,
                *meta.local_many_to_many,
            )
            excluded.update(
                field.name
                for field in own_fields
                if getattr(field, "concrete", True) is False
                and not field.is_relation
                and not getattr(field, "auto_created", False)
            )
        return sorted(excluded)

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
        flushed. The method records its own revert revision so integrity does
        not depend on the caller's transport opening a reversion block.
        """

        data = version.field_dict
        reverted: list[str] = []
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
                reverted.append(name)
        if not reverted:
            return
        with reversion.create_revision():
            self.save(update_fields=update_fields_with_auto_now(self, reverted))
            reversion.set_comment(f"Reverted to revision {version.revision_id}.")
