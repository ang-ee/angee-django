"""Small abstract model mixins shared by source addons."""

from __future__ import annotations

from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from rebac import RebacMixin


class TimestampMixin(models.Model):
    """Add creation and update timestamps to a source model."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        """Django model options."""

        abstract = True


class AngeeModel(TimestampMixin, RebacMixin):
    """Default abstract base for composed Angee source models.

    Composing :class:`rebac.RebacMixin` gives every Angee model the
    REBAC-scoped manager and per-instance actor binding. A model stays an
    unscoped pass-through until it declares ``Meta.rebac_resource_type``
    (optionally ``rebac_id_attr``); from then on reads, writes, and field
    access are gated by the permission schema.
    """

    class Meta:
        """Django model options."""

        abstract = True

    @classmethod
    def get_composition_label(cls) -> str:
        """Return the normalized composition label for this source model."""

        return cls._meta.label_lower

    @classmethod
    def get_extension_target(cls) -> str | None:
        """Return the normalized model this source model extends, if any."""

        target = getattr(cls, "extends", None)
        if target in {None, ""}:
            return None
        return cls.normalize_model_label(str(target))

    @classmethod
    def normalize_model_label(cls, label: str) -> str:
        """Return a normalized ``app_label.model_name`` reference."""

        try:
            app_label, model_name = make_model_tuple(label)
        except ValueError as exc:
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__name__}.extends must be "
                "'app_label.ModelName'"
            ) from exc
        return f"{app_label}.{model_name}"

    @classmethod
    def get_declared_composition_fields(cls) -> tuple[str, ...]:
        """Return fields this source model declares for composition."""

        local_names = {
            field.name
            for field in (
                *cls._meta.local_fields,
                *cls._meta.local_many_to_many,
            )
        }
        inherited_names: set[str] = set()
        for base in cls.__mro__[1:]:
            meta = getattr(base, "_meta", None)
            if (
                not issubclass(base, models.Model)
                or meta is None
                or not meta.abstract
            ):
                continue
            inherited_names.update(
                field.name
                for field in (
                    *meta.local_fields,
                    *meta.local_many_to_many,
                )
            )
        return tuple(sorted(local_names - inherited_names))

    @classmethod
    def get_model_reference(cls) -> str:
        """Return a readable dotted reference to this model class."""

        return f"{cls.__module__}.{cls.__name__}"

    @classmethod
    def get_extension_bases(cls) -> tuple[type[models.Model], ...]:
        """Return abstract bases this extension contributes to a target model.

        Extension marker classes may inherit field/behavior mixins and carry
        only ``extends`` themselves. If no contributed base exists, the
        extension class itself is the contribution, preserving direct
        field-bearing extension classes.
        """

        contributed = tuple(
            base
            for base in cls.__bases__
            if (
                isinstance(base, type)
                and issubclass(base, models.Model)
                and base not in {models.Model, TimestampMixin, AngeeModel}
            )
        )
        return contributed or (cls,)

    @property
    def public_id(self) -> str:
        """Return the stable external id for this model instance."""

        return str(self.pk)

    @classmethod
    def from_public_id(cls, value: str) -> Any | None:
        """Return the row with this external id or ``None``."""

        return cls._default_manager.filter(pk=value).first()


class SqidMixin(models.Model):
    """Lookup helper for models with an explicit ``sqid`` field."""

    class Meta:
        """Django model options."""

        abstract = True

    @classmethod
    def from_sqid(cls, sqid: str) -> Any | None:
        """Return the row with ``sqid`` or ``None``."""

        return cls._default_manager.filter(sqid=sqid).first()

    @property
    def public_id(self) -> str:
        """Return the opaque sqid for this model instance."""

        return str(self.sqid)

    @classmethod
    def from_public_id(cls, value: str) -> Any | None:
        """Return the row with this opaque external id or ``None``."""

        return cls.from_sqid(value)


class HistoryMixin(models.Model):
    """Marker: audit a source model with django-simple-history.

    The composer emits ``HistoricalRecords`` onto the composed concrete model
    (with its app label), so each save appends to a ``Historical<Model>``
    shadow table exposed as ``instance.history``. Addons just mix this in.
    """

    class Meta:
        """Django model options."""

        abstract = True


class RevisionMixin(models.Model):
    """Snapshot named fields into django-reversion versions.

    A model declares ``revisioned_fields``; the base addon registers the
    concrete model with django-reversion so edits made inside a revision block
    (every request, via the revision middleware) are versioned and revertible.
    Use this for large content fields that would bloat the history table.
    """

    revisioned_fields: ClassVar[tuple[str, ...]] = ()

    class Meta:
        """Django model options."""

        abstract = True

    @property
    def revisions(self) -> Any:
        """Return this row's versions, newest first."""

        import reversion

        return reversion.models.Version.objects.get_for_object(self)

    def revert_to(self, version: Any) -> None:
        """Restore the revisioned fields from a version and save.

        Only the declared fields are versioned, so the row is restored field by
        field rather than through a whole-object deserialization.
        """

        data = version.field_dict
        for name in self.revisioned_fields:
            if name in data:
                setattr(self, name, data[name])
        self.save()


def register_revision_models() -> None:
    """Register every composed model declaring ``revisioned_fields``.

    Run from the base addon's ``ready()`` once concrete models are loaded, so
    django-reversion tracks the runtime models, not the abstract sources.
    """

    import reversion
    from django.apps import apps

    for model in apps.get_models():
        fields = getattr(model, "revisioned_fields", ())
        if fields and not reversion.is_registered(model):
            reversion.register(model, fields=list(fields))
