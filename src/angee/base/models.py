"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

from typing import Any, Self, TypeVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from rebac import RebacMixin
from rebac.managers import RebacManager, RebacQuerySet

from angee.base.mixins import SqidMixin, TimestampMixin

_ModelT = TypeVar("_ModelT", bound=models.Model)


class AngeeQuerySet(RebacQuerySet[_ModelT]):
    """QuerySet API shared by Angee source and runtime models."""

    def apply_ambient_scope(self) -> Self:
        """Eagerly apply REBAC row scope using the queryset or ambient actor."""

        self._apply_scope_in_place()
        return self


class AngeeManager(RebacManager.from_queryset(AngeeQuerySet)):  # type: ignore[misc]
    """Manager backed by AngeeQuerySet."""

    def get_queryset(self) -> AngeeQuerySet[Any]:
        """Return the base Angee queryset for this manager's model."""

        return cast(AngeeQuerySet[Any], super().get_queryset())


class AngeeModel(TimestampMixin, RebacMixin):
    """Abstract base model for Angee source and runtime models."""

    objects = AngeeManager()
    """Default REBAC manager with Angee queryset conveniences."""

    extends: str | None = None
    """Optional ``app_label.ModelName`` target this source model extends."""

    _composer_emits: bool = True
    """Per-class opt-out of runtime emission, read only via ``is_composer_emitted``.

    The read is non-inherited, so this ``True`` documents the default rather than
    acting as one: a base opts out with ``_composer_emits = False`` in its own
    body, and that opt-out does not carry to subclasses.
    """

    class Meta:
        """Django model options for Angee's abstract model base."""

        abstract = True

    @classmethod
    def get_composition_label(cls) -> str:
        """Return this model's normalized composition label."""

        return cls._meta.label_lower

    @classmethod
    def is_composer_emitted(cls) -> bool:
        """Return whether the composer emits a concrete table for this model.

        Read **non-inherited** from the declaring class — deliberately unlike
        ``extends``, which is inherited. An abstract base that exists only for
        other source models to subclass (e.g. ``integrate.Capability``) sets
        ``_composer_emits = False`` to stay out of emission; because the read is
        non-inherited, a concrete subclass that does not re-declare it still
        emits, and each such base opts out for itself.
        """

        return cls.__dict__.get("_composer_emits", True)

    @classmethod
    def get_extension_target(cls) -> str | None:
        """Return the normalized model label this source model extends."""

        target = cls.extends
        if target is None:
            return None
        if not isinstance(target, str):
            raise ImproperlyConfigured(f"{cls.__module__}.{cls.__name__}.extends must be a string.")
        try:
            app_label, model_name = make_model_tuple(target)
        except ValueError as error:
            raise ImproperlyConfigured(
                f"{cls.__module__}.{cls.__name__}.extends must be an 'app_label.ModelName' reference."
            ) from error
        return f"{app_label}.{model_name}"

    @classmethod
    def get_extension_bases(cls) -> tuple[type[models.Model], ...]:
        """Return abstract model bases contributed by this extension."""

        if cls.get_extension_target() is None:
            return ()

        bases = tuple(base for base in cls.__bases__ if _is_contributed_extension_base(base))
        return bases or (cls,)

    @property
    def public_id(self) -> str:
        """Return the stable public identifier for this model instance."""

        value = _public_id_value(self)
        if value in (None, ""):
            return ""
        return str(value)

    @classmethod
    def from_public_id(cls, value: str) -> Self | None:
        """Return the instance addressed by ``value``, if one exists."""

        if value == "":
            return None

        lookup = cls.public_id_lookup(value)
        try:
            instance = cls._default_manager.filter(**lookup).first()
        except TypeError, ValueError:
            return None
        return cast(Self | None, instance)

    @classmethod
    def public_id_lookup(cls, value: str) -> dict[str, Any]:
        """Return the Django lookup for this model's public identifier."""

        if issubclass(cls, SqidMixin):
            return {"sqid": value}
        return {cls._meta.pk.name: value}


def instance_from_public_id(model: type[_ModelT], value: str) -> _ModelT | None:
    """Return ``model`` instance addressed by Angee or Django public ID."""

    if issubclass(model, AngeeModel):
        return cast(_ModelT | None, model.from_public_id(value))

    try:
        instance = model._default_manager.filter(pk=value).first()
    except TypeError, ValueError:
        return None
    return cast(_ModelT | None, instance)


def public_id_of(instance: models.Model) -> str:
    """Return the Angee public ID or Django primary key for ``instance``."""

    if isinstance(instance, AngeeModel):
        return instance.public_id
    if instance.pk is None:
        return ""
    return str(instance.pk)


def _is_contributed_extension_base(value: type) -> bool:
    """Return whether ``value`` is an abstract model extension base."""

    if not issubclass(value, models.Model):
        return False
    if value in {models.Model, TimestampMixin, RebacMixin, AngeeModel}:
        return False
    model = cast(type[models.Model], value)
    meta = model._meta
    return bool(meta.abstract)


def _public_id_value(instance: models.Model) -> Any:
    """Return the raw public identifier value owned by ``instance``."""

    if isinstance(instance, SqidMixin):
        return instance.sqid
    return instance.pk
