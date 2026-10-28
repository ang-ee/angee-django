"""Django field owning trusted implementation-key selection and row accessors."""
from __future__ import annotations

from collections.abc import Sequence
from functools import partialmethod
from typing import Any, cast
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from django_choices_field import TextChoicesField
from angee.base.fields.state import enum_member_for
from angee.base.implementations import (
    ImplChoice, _impl_choices_enum, declared_impl_registries, impl_choices, impl_choices_enum,
    impl_registry, resolve_impl_class,
)

class ImplClassField[T = object](TextChoicesField):
    """A column naming a non-model implementation class by a short key.

    The base names the Django setting that maps keys to dotted import paths.
    Addons contribute impls into that setting through autoconfig, making
    the key set closed at composition time. The field renders as a
    ``TextChoices`` enum and resolves only configured, trusted paths.
    """

    def __init__(
        self,
        base_class: type[T] | None = None,
        *,
        registry_setting: str = "",
        create_only: bool = False,
        **kwargs: Any,
    ) -> None:
        """Bind the implementation base and build the enum from the registry keys."""

        if base_class is not None and not isinstance(base_class, type):
            raise ImproperlyConfigured("ImplClassField base_class must be a type.")
        if base_class is not None and registry_setting:
            raise ImproperlyConfigured("ImplClassField registry_setting is only for historical migration fields.")
        self.base_class = base_class
        self.registry_setting = getattr(base_class, "registry_setting") if base_class is not None else registry_setting
        self.create_only = create_only
        self._historical_default = kwargs.get("default")
        kwargs.setdefault("max_length", 100)
        super().__init__(choices_enum=self._build_enum(), **kwargs)

    def deconstruct(self) -> tuple[str, str, Sequence[Any], dict[str, Any]]:
        """Emit a plain varchar column and rebuild the enum from settings on reconstruct."""

        name, path, args, kwargs = super().deconstruct()
        kwargs.pop("choices", None)
        kwargs["registry_setting"] = self.registry_setting
        if self.create_only:
            kwargs["create_only"] = True
        return name, path, args, kwargs

    def check(self, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate the field's base declaration; the base check owns registry content."""

        errors = super().check(**kwargs)
        if not isinstance(self.base_class, type):
            errors.append(
                checks.Error(
                    "ImplClassField requires a base_class type.",
                    hint="Pass the implementation base as the first argument.",
                    obj=self,
                    id="angee.E001",
                )
            )
        if not self.registry_setting:
            errors.append(
                checks.Error(
                    "ImplClassField requires registry_setting naming the key->path mapping.",
                    obj=self,
                    id="angee.E002",
                )
            )
        elif isinstance(self.base_class, type) and (
            f"{self.base_class.__module__}.{self.base_class.__qualname__}" not in declared_impl_registries()
        ):
            errors.append(
                checks.Error(
                    f"{self.base_class.__name__} is not declared in settings.ANGEE_IMPL_REGISTRIES.",
                    obj=self,
                    id="angee.E032",
                )
            )
        return errors

    @classmethod
    def for_model(cls, model: type[models.Model], field_name: str) -> ImplClassField[object]:
        """Return the typed implementation field a model declares."""
        field = model._meta.get_field(field_name)
        if not isinstance(field, cls):
            raise FieldDoesNotExist(f"{model.__name__}.{field_name} is not an ImplClassField.")
        return field

    def contribute_to_class(self, cls: type[models.Model], name: str, **kwargs: Any) -> None:
        """Bind a native get_<field>_impl accessor while Django builds the model."""
        super().contribute_to_class(cls, name, **kwargs)
        accessor = f"get_{self.name}_impl"
        if accessor in cls.__dict__:
            raise ImproperlyConfigured(f"{cls.__name__}.{accessor} collides with implementation field {name!r}.")
        setattr(cls, accessor, partialmethod(_resolve_bound_implementation, field=self))

    def resolve_class(self, key: Any, *, default: str | None = None) -> type[T]:
        """Return the impl class the configured mapping binds to ``key``."""

        if self.base_class is None:
            raise ImproperlyConfigured("Historical implementation fields do not resolve runtime classes.")
        selected = self.key_for(key if key is not None else default)
        if not selected and default is not None:
            selected = self.key_for(default)
        return resolve_impl_class(self.base_class, selected)

    def registered_keys(self) -> tuple[str, ...]:
        """Return this field's configured implementation keys in deterministic order."""

        return tuple(sorted(impl_registry(self.registry_setting)))

    def resolve_for(self, instance: models.Model, *, default: str | None = None) -> type[T]:
        """Return the impl class selected by this field on ``instance``."""

        return self.resolve_class(getattr(instance, self.attname), default=default)

    def key_for(self, value: Any) -> str:
        """Return the canonical registry key for a stored/input enum-ish value."""

        if value in (None, ""):
            return ""
        member = enum_member_for(cast(Any, self.choices_enum), value)
        if member is not None:
            return str(member.value)
        return str(getattr(value, "value", value)).strip()

    def _build_enum(self) -> type[models.TextChoices]:
        """Return a ``TextChoices`` enum over the registered keys, in deterministic order."""

        if self.base_class is None:
            # Migration-state fields retain their serialized setting, even after
            # the active base and its setting have changed or been retired.
            keys = tuple(sorted(impl_registry(self.registry_setting)))
            if not keys:
                default = self._historical_default
                keys = (default if isinstance(default, str) and default else "historical",)
            return _impl_choices_enum(self.registry_setting, keys)
        return impl_choices_enum(self.base_class)

    def impl_choices(self) -> list[ImplChoice]:
        """Return pickable choices for the registry in deterministic key order."""

        return impl_choices(cast(type, self.base_class))


def _resolve_bound_implementation[T](
    instance: models.Model, *, field: ImplClassField[T], default: str | None = None,
) -> type[T]:
    return field.resolve_for(instance, default=default)
