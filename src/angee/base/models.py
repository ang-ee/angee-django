"""Source models and the default base model for Angee addons."""

from __future__ import annotations

from typing import Self

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from rebac import RebacMixin

from angee.base.mixins import TimestampMixin


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
    def from_public_id(cls, value: str) -> Self | None:
        """Return the row with this external id or ``None``."""

        return cls._default_manager.filter(pk=value).first()


def instance_from_public_id(
    model: type[models.Model], value: str
) -> models.Model | None:
    """Return the row of ``model`` named by its public id.

    An :class:`AngeeModel` target resolves through its ``from_public_id``
    contract; a plain Django target (such as ``auth.User`` referenced by a
    resource row) falls back to its primary key. Both forms live here once so
    callers never re-decide it from outside.
    """

    if issubclass(model, AngeeModel):
        return model.from_public_id(value)
    return model._default_manager.filter(pk=value).first()


def public_id_of(instance: models.Model) -> str:
    """Return an instance's public id.

    Mirrors :func:`instance_from_public_id`: an :class:`AngeeModel` answers
    with its ``public_id`` property, while a plain Django target falls back to
    its primary key.
    """

    if isinstance(instance, AngeeModel):
        return instance.public_id
    return str(instance.pk)


__all__ = ["AngeeModel", "instance_from_public_id", "public_id_of"]
