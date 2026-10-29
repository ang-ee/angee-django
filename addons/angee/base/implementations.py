"""Keyed implementation declarations, native config schemas and registry checks."""
from __future__ import annotations

import copy
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any, ClassVar, cast

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils.module_loading import import_string
from pydantic import BaseModel

from angee.base.schemas.forms import config_form_spec
from angee.base.validators import validate_config_aliases, validate_value


@dataclass(frozen=True, slots=True)
class ImplChoice:
    """Pickable implementation metadata shared by GraphQL and form defaults."""

    key: str
    label: str
    icon: str
    category: str
    defaults: dict[str, Any]
    config_schema: dict[str, Any] | None


class ImplBase:
    """Base for a keyed implementation selected by a field or rowless contract.

    Registry bases name their composed setting. Subclasses declare class-level
    ``key``/``label``/``icon``/``category`` and a ``defaults`` mapping of model-field
    values to seed. Behaviour lives on the
    domain subclass (e.g. ``IntegrationImpl``, ``OAuthProviderType``).
    """

    key: ClassVar[str] = ""
    registry_setting: ClassVar[str] = ""
    label: ClassVar[str] = ""
    icon: ClassVar[str] = ""
    category: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}
    config_model: ClassVar[type[BaseModel] | None] = None
    config_form_spec_json_fields: ClassVar[bool] = False
    """Render unsupported non-root config shapes through the JSON control."""
    check_config_form_spec: ClassVar[bool] = True
    """False for rowless contracts whose typed config is never offered as a FormSpec."""

    @classmethod
    def effective_defaults(cls, *, config_schema: dict[str, Any] | None = None) -> dict[str, Any]:
        """Return this impl's defaults merged along the MRO, with derived values winning.

        Dict-valued defaults merge one level deep, so a refinement adds keys to
        its base's dict default instead of replacing it; scalar values are
        overridden outright.
        """

        merged: dict[str, Any] = {}
        for base in reversed(cls.__mro__):
            own = base.__dict__.get("defaults")
            if not own:
                continue
            for field_name, value in own.items():
                current = merged.get(field_name)
                if isinstance(current, dict) and isinstance(value, dict):
                    merged[field_name] = {**current, **value}
                else:
                    merged[field_name] = value
        config_defaults = _config_defaults(config_schema if config_schema is not None else cls.config_json_schema())
        if config_defaults:
            inherited = merged.get("config")
            merged["config"] = {
                **(inherited if isinstance(inherited, dict) else {}),
                **config_defaults,
            }
        return merged

    @classmethod
    def config_defaults(cls) -> dict[str, Any]:
        """Return non-empty static input suggestions from Pydantic's JSON Schema.

        Pydantic's validation schema owns default encoding and omits factories;
        ``parse_config`` resolves those when validating runtime input.
        FormSpec support does not determine which backend defaults are available.
        """

        return _config_defaults(cls.config_json_schema())

    @classmethod
    def config_json_schema(cls) -> dict[str, Any] | None:
        """Read the one checked Pydantic wire contract for defaults and forms."""
        if cls.config_model is None:
            return None
        validate_config_aliases(cls.config_model, owner=cls.__name__)
        return cls.config_model.model_json_schema(by_alias=True)

    @classmethod
    def display_label(cls) -> str:
        """Return this impl's own label, falling back to a title-cased key."""

        own_label = cls.__dict__.get("label")
        if own_label:
            return str(own_label)
        return cls.key.replace("_", " ").title() if cls.key else (cls.label or cls.__name__)

    @classmethod
    def choice(cls) -> ImplChoice:
        """Return this impl's pickable choice metadata for forms."""

        schema = cls.config_json_schema()
        return ImplChoice(
            key=cls.key,
            label=cls.display_label(),
            icon=cls.icon,
            category=cls.category,
            defaults=cls.effective_defaults(config_schema=schema),
            config_schema=cls.config_form_spec(config_schema=schema),
        )

    @classmethod
    def parse_config(cls, value: Any) -> BaseModel | None:
        """Return the declared config model, rejecting non-empty config without one."""

        if cls.config_model is None:
            if value:
                raise ValidationError({"config": f"{cls.__name__} does not accept configuration."})
            return None
        return cast(BaseModel, validate_value(cls.config_model, value, field="config"))

    @classmethod
    def normalize_config(cls, value: Any) -> dict[str, Any]:
        """Parse config once and serialize the model with its declared wire aliases."""

        validated = cls.parse_config(value)
        return validated.model_dump(mode="json", by_alias=True) if validated is not None else {}

    @classmethod
    def config_form_spec(cls, *, config_schema: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Translate Pydantic's supported JSON Schema subset into FormSpec."""

        schema = cls.config_json_schema() if config_schema is None else config_schema
        return (
            config_form_spec(schema, owner=cls.__name__, json_fields=cls.config_form_spec_json_fields)
            if schema is not None
            else None
        )


def impl_registry(registry_setting: str) -> dict[str, str]:
    """Return the configured ``key -> dotted path`` mapping for ``registry_setting``."""

    mapping = getattr(settings, registry_setting, {}) if registry_setting else {}
    if not isinstance(mapping, Mapping):
        raise ImproperlyConfigured(f"settings.{registry_setting} must be a mapping of key to dotted path.")
    return {str(key): str(value) for key, value in mapping.items()}


def resolve_impl_class[T](base_class: type[T], key: str) -> type[T]:
    """Return the impl class the base's named registry binds to ``key``.

    The dotted path comes from composed, trusted settings and is checked against
    ``base_class`` before returning.
    """

    registry_setting = getattr(base_class, "registry_setting")
    registry = impl_registry(registry_setting)
    try:
        dotted = registry[key]
    except KeyError as error:
        known = ", ".join(sorted(registry)) or "none configured"
        raise ImproperlyConfigured(
            f"No impl for key {key!r} in settings.{registry_setting} (known: {known})."
        ) from error
    impl = import_string(dotted)
    if not (isinstance(base_class, type) and isinstance(impl, type) and issubclass(impl, base_class)):
        base_name = getattr(base_class, "__name__", base_class)
        raise ImproperlyConfigured(f"settings.{registry_setting}[{key!r}] = {dotted!r} is not a {base_name}.")
    declared_key = getattr(impl, "key", None)
    if declared_key is not None and declared_key != key:
        raise ImproperlyConfigured(
            f"settings.{registry_setting}[{key!r}] resolves {impl.__name__} with key {declared_key!r}."
        )
    return impl


def resolve_all_impl_classes[T](
    base_class: type[T],
    *,
    on_error: Callable[[Exception], None] | None = None,
) -> tuple[type[T], ...]:
    """Resolve and validate every configured impl in deterministic key order.

    Implementations that declare class keys must agree with their registry keys.
    Native implementation classes without that contract use the registry key
    alone. System-check callers may supply ``on_error`` to collect every
    invalid declaration while ordinary callers retain fail-fast resolution.
    """

    return tuple(impl for _key, impl in _impl_classes(base_class, on_error=on_error))


def _impl_classes[T](
    base_class: type[T], *, on_error: Callable[[Exception], None] | None = None,
) -> Iterator[tuple[str, type[T]]]:
    """Enumerate trusted keyed classes once for checks and metadata projections."""

    registry_setting = getattr(base_class, "registry_setting")
    for key in sorted(impl_registry(registry_setting)):
        try:
            impl = resolve_impl_class(base_class, key)
        except (ImportError, ImproperlyConfigured) as error:
            if on_error is None:
                raise
            on_error(error)
            continue
        yield key, impl


def check_impl_registry(
    base_class: type[object],
    *,
    obj: object | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Check registry declarations and offered config forms, including rowless registries.

    Empty registries are valid catalogues. Model fields and enum projections
    require entries, and consumers own any required selected-key policy.
    """

    del kwargs
    errors: list[checks.CheckMessage] = []
    try:
        classes = resolve_all_impl_classes(
            base_class,
            on_error=lambda error: errors.append(
                checks.Error(
                    str(error),
                    obj=obj,
                    id="angee.E003" if isinstance(error, ImportError) else "angee.E004",
                )
            ),
        )
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), obj=obj, id="angee.E002")]
    for impl in classes:
        if issubclass(impl, ImplBase) and impl.check_config_form_spec:
            try:
                impl.config_form_spec()
            except ImproperlyConfigured as error:
                errors.append(checks.Error(str(error), obj=obj, id="angee.E005"))
    return errors


def impl_choices(
    base_class: type[object], *, include: Callable[[type[object]], bool] | None = None,
) -> list[ImplChoice]:
    """Project pickable metadata in registry-key order, without requiring a column."""

    choices: list[ImplChoice] = []
    for key, impl in _impl_classes(base_class):
        if include is not None and not include(impl):
            continue
        if issubclass(impl, ImplBase):
            choices.append(replace(impl.choice(), key=key))
        else:
            choices.append(ImplChoice(key=key, label=key, icon="", category="", defaults={}, config_schema=None))
    return choices


def _config_defaults(schema: dict[str, Any] | None) -> dict[str, Any]:
    if schema is None:
        return {}
    return {
        name: copy.deepcopy(field["default"])
        for name, field in schema["properties"].items()
        if field.get("default") not in (None, "")
    }
