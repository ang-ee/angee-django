"""Runtime model primitives shared by composed Angee applications."""

from __future__ import annotations

from typing import Any, Self, TypeVar, cast

from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.db.models.utils import make_model_tuple
from rebac import RebacMixin
from rebac.managers import RebacManager, RebacQuerySet

from angee.base.mixins import TimestampMixin

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

    runtime: bool = False
    """Whether this abstract source model materializes into the generated runtime.

    The read is non-inherited: an abstract base can stay ``runtime = False`` and
    a concrete source subclass opts in by declaring ``runtime = True`` itself.
    Extensions use ``extends`` instead of this flag.
    """

    class Meta:
        """Django model options for Angee's abstract model base."""

        abstract = True

    @classmethod
    def get_composition_label(cls) -> str:
        """Return this model's normalized composition label."""

        return cls._meta.label_lower

    @classmethod
    def is_runtime_model(cls) -> bool:
        """Return whether this model class declares itself as a runtime model."""

        return cls.__dict__.get("runtime", False)

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

        value = self.public_id_value()
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

        return {cls._meta.pk.name: value}

    def public_id_value(self) -> Any:
        """Return the raw public identifier value owned by this instance."""

        return self.pk


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
