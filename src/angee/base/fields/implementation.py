"""Django field owning trusted implementation-key selection and row accessors."""

from __future__ import annotations

import copyreg
from collections.abc import Sequence
from enum import Enum
from functools import cache, partialmethod
from typing import Any, cast

from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django_choices_field import TextChoicesField

from angee.base.fields.state import enum_member_for
from angee.base.implementations import (
    RegistryBase,
    check_impl_registry,
    impl_registry,
    resolve_impl_class,
)


class ImplClassField[T: RegistryBase = RegistryBase](TextChoicesField):
    """A column naming a non-model implementation class by a short key.

    The base names the Django setting that maps keys to dotted import paths.
    Addons contribute impls into that setting through autoconfig, making
    the key set closed at composition time. The field renders as a
    ``TextChoices`` enum and resolves only configured, trusted paths.

    A stored key its choices do not name (one a later release retired, or one
    another process's registry holds) still loads, lists and filters, as its
    text; no value the field prepares writes one, and resolving it raises. Its
    GraphQL read is null, so the row's list and record still answer; the row
    saves again once a registered implementation is chosen.
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
        self.registry_setting = base_class.registry_setting if base_class is not None else registry_setting
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
        """Validate this selector and every entry of its bound implementation mapping."""

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
        elif isinstance(self.base_class, type):
            errors.extend(check_impl_registry(self.base_class, obj=self))
        return errors

    def contribute_to_class(
        self,
        cls: type[models.Model],
        name: str,
        private_only: bool = False,
    ) -> None:
        """Bind a native get_<field>_impl accessor while Django builds the model."""
        super().contribute_to_class(cls, name, private_only=private_only)
        accessor = f"get_{self.name}_impl"
        if accessor in cls.__dict__:
            raise ImproperlyConfigured(f"{cls.__name__}.{accessor} collides with implementation field {name!r}.")
        setattr(cls, accessor, partialmethod(_resolve_bound_implementation, field=self))

    refuses_unknown_keys = True
    """Whether writing the column refuses a key outside this field's choices.

    A history copy records the key its live row saved, so its owner turns this off.
    """

    def has_key(self, value: Any) -> bool:
        """Whether ``value`` names one of this field's choices."""

        return self.key_for(value) in {member.value for member in self.choices_enum}

    def invalid_key(self, value: Any) -> ValidationError:
        """The field's plain refusal of ``value``, naming the key and nothing internal."""

        return ValidationError(self.error_messages["invalid_choice"], code="invalid_choice", params={"value": value})

    def to_python(self, value: Any) -> Any:
        """Convert an input value as the enum field does, refusing a key outside the choices with the plain message.

        Input reaches the field here: ``full_clean``, a model form, a
        deserialized fixture, a source statement's members. Loading and lookups
        do not (:meth:`from_db_value`, :meth:`get_prep_value`).
        """

        if value not in self.empty_values and not self.has_key(value):
            raise self.invalid_key(value)
        return super().to_python(value)

    def from_db_value(self, value: Any, expression: Any, connection: Any) -> Any:
        """Load a stored key as its member, or as the stored text once the choices no longer name it."""

        if value in self.empty_values:
            return None
        try:
            return self.choices_enum(value)
        except ValueError:
            return value

    def get_prep_value(self, value: Any) -> Any:
        """Prepare a value as its canonical key (:meth:`key_for`): the member where the choices name it, else its text.

        ``" base "`` and the member's name ``"BASE"`` both prepare as ``base``,
        so a filter finds the rows that store a key whichever spelling it is
        given, and one on a key the choices no longer name finds those rows too;
        :meth:`get_db_prep_save` refuses to write such a key. Input keeps
        :meth:`to_python`'s stricter conversion.
        """

        if value in self.empty_values:
            return super().get_prep_value(value)
        key = self.key_for(models.Field.get_prep_value(self, value))
        return self.choices_enum(key) if self.has_key(key) else key

    def get_db_prep_save(self, value: Any, connection: Any) -> Any:
        """Refuse writing a key outside this field's choices, in every value the field prepares.

        That is a plain value in an insert, an update, ``QuerySet.update`` or
        ``bulk_update``, and a ``Value`` whose output field is this one. An
        expression is not checked: ``F``, ``Subquery``, ``Cast``, ``Case``, or
        a ``Value`` without this field as its output writes what it computes.
        The write runs after the save's REBAC gates, so a caller who may not
        save the row gets that refusal. A stored row saves a key its choices
        name, even once the registry dropped it; a save that leaves the column
        out writes nothing here. A history copy records its row's key and a
        historical migration field keeps whatever it stored.
        """

        if (
            self.refuses_unknown_keys
            and self.base_class is not None
            and not hasattr(value, "as_sql")
            and value not in self.empty_values
            and not self.has_key(value)
        ):
            raise ValidationError({self.name: self.invalid_key(value)})
        return super().get_db_prep_save(value, connection)

    def resolve_class(self, key: Any, *, default: str | None = None) -> type[T]:
        """Return the impl class the configured mapping binds to ``key``.

        A key the mapping does not bind raises ``ImproperlyConfigured`` naming
        this field, the key and the registry's setting, where the class is
        needed: never at load.
        """

        if self.base_class is None:
            raise ImproperlyConfigured("Historical implementation fields do not resolve runtime classes.")
        selected = self.key_for(key if key is not None else default)
        if not selected and default is not None:
            selected = self.key_for(default)
        return resolve_impl_class(self.base_class, selected, owner=str(self))

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
        return str(value.value if isinstance(value, Enum) else value).strip()

    def _build_enum(self) -> type[models.TextChoices]:
        """Return a ``TextChoices`` enum over the registered keys, in deterministic order."""

        if self.base_class is None:
            # Migration-state fields retain their serialized setting, even after
            # the active base and its setting have changed or been retired.
            try:
                keys = tuple(sorted(impl_registry(self.registry_setting)))
            except AttributeError:
                # Django's retained migration state outlives retired addon settings.
                keys = ()
            if not keys:
                default = self._historical_default
                keys = (default if isinstance(default, str) and default else "historical",)
            return _impl_choices_enum(self.registry_setting, keys)
        return impl_choices_enum(self.base_class)


def _resolve_bound_implementation[T: RegistryBase](
    instance: models.Model,
    *,
    field: ImplClassField[T],
    default: str | None = None,
) -> type[T]:
    return field.resolve_for(instance, default=default)


def impl_choices_enum(base_class: type[RegistryBase]) -> type[models.TextChoices]:
    """Return the shared native enum for the current non-empty registry key set."""

    registry_setting = base_class.registry_setting
    keys = tuple(sorted(impl_registry(registry_setting)))
    if not keys:
        raise ImproperlyConfigured(
            f"Implementation registry settings.{registry_setting} is empty; "
            "at least one implementation is required to build its enum."
        )
    return _impl_choices_enum(registry_setting, keys)


@cache
def _impl_choices_enum(registry_setting: str, keys: tuple[str, ...]) -> type[models.TextChoices]:
    """Share enum identity across projections while settings changes select new keys.

    The enum is built at runtime, so no module holds it by name, as one holds a
    ``TextChoices`` whose members pickle by reference. Each member pickles
    instead, through :func:`copyreg.pickle`, as its registry, key set and value
    (:func:`_impl_choice`), and unpickles without reading settings, as that very
    member in any process with the same key set. A queryset, a loaded row, or a
    compiled permission statement holding an implementation key then pickles too.

    A process whose registry holds other keys unpickles a member of an enum over
    the pickling process's keys: it equals and hashes as its key, and the field
    takes it as that key's text. A key its choices name becomes the current
    member; any other filters as its text and is refused as input or as a write.
    A pickler with a private ``dispatch_table`` that leaves out copyreg's does
    not see the reduction and refuses the member.
    """

    core = registry_setting.removeprefix("ANGEE_").removesuffix("_CLASSES")
    camel = "".join(part.capitalize() for part in core.split("_") if part)
    members = [(key.upper(), (key, key)) for key in keys]
    enum = models.TextChoices(f"{camel or 'Impl'}Impl", members)
    copyreg.pickle(enum, lambda member: (_impl_choice, (registry_setting, keys, member.value)))
    return enum


def _impl_choice(registry_setting: str, keys: tuple[str, ...], value: str) -> models.TextChoices:
    """Return the member ``value`` of the enum over ``keys``: how an implementation key unpickles.

    Every stored pickle of a key names this function by its dotted path, and
    cache entries outlive a deploy: keep the name, or leave an alias when it moves.
    """

    return _impl_choices_enum(registry_setting, keys)(value)
