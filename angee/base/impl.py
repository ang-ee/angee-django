"""Registry-backed implementation selection for Angee models and settings.

This module is the single owner of the impl mechanism: the model field that
stores a selected key, the metadata base classes impls subclass, and the
settings-backed registry resolver shared by row-owned and row-less selectors.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from functools import cache
from typing import Any, ClassVar, cast

from django.conf import settings
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from pydantic import BaseModel
from rebac import system_context

from angee.base.fields import enum_member_for
from angee.base.validation import validate_value
from angee.data.form_specs import _validate_config_aliases, model_config_form_spec

__all__ = [
    "ImplBase",
    "ImplChoice",
    "ImplClassField",
    "ImplDefaultsMixin",
    "check_impl_registry",
    "impl_choices",
    "impl_choices_enum",
    "impl_registry",
    "resolve_all_impl_classes",
    "resolve_hook",
    "resolve_hooks",
    "resolve_impl_class",
]


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
    def effective_defaults(cls) -> dict[str, Any]:
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
        config_defaults = cls.config_defaults()
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

        if cls.config_model is None:
            return {}
        _validate_config_aliases(cls.config_model, owner=cls.__name__)
        schema = cls.config_model.model_json_schema(by_alias=True)
        return {
            name: copy.deepcopy(field["default"])
            for name, field in schema["properties"].items()
            if field.get("default") not in (None, "")
        }

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

        return ImplChoice(
            key=cls.key,
            label=cls.display_label(),
            icon=cls.icon,
            category=cls.category,
            defaults=cls.effective_defaults(),
            config_schema=cls.config_form_spec(),
        )

    @classmethod
    def parse_config(cls, value: Any) -> BaseModel | None:
        """Return the declared config model, rejecting non-empty config without one."""

        if cls.config_model is None:
            if value:
                raise ValidationError({"config": f"{cls.__name__} does not accept configuration."})
            return None
        return cast(BaseModel, cls.parse_value(value, cls.config_model, "config"))

    @classmethod
    def normalize_config(cls, value: Any) -> dict[str, Any]:
        """Parse config once and serialize the model with its declared wire aliases."""

        validated = cls.parse_config(value)
        return validated.model_dump(mode="json", by_alias=True) if validated is not None else {}

    @classmethod
    def parse_value(cls, value: Any, type_: Any, path: str) -> Any:
        """Validate through the cached native adapter, translating Django field paths."""

        return validate_value(type_, value, field=path)

    @classmethod
    def declared_config_keys(cls) -> frozenset[str] | None:
        """Return the top-level config wire names accepted by model-row validation.

        ``None`` means row config is untyped or open (``extra="allow"``), so
        every key is accepted. Explicit typed parsing uses ``parse_config``.
        """

        if cls.config_model is None or cls.config_model.model_config.get("extra") == "allow":
            return None
        return frozenset(field.alias or name for name, field in cls.config_model.model_fields.items())

    @classmethod
    def config_form_spec(cls) -> dict[str, Any] | None:
        """Translate Pydantic's supported JSON Schema subset into FormSpec."""

        if cls.config_model is None:
            return None
        return model_config_form_spec(
            cls.config_model,
            owner=cls.__name__,
            json_fields=cls.config_form_spec_json_fields,
        )

    @classmethod
    def materialize(
        cls,
        instance: models.Model,
        *,
        provided: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Seed ``instance``'s fields from this impl's effective defaults on create.

        Seeds only fields the caller did not supply. A string foreign-key default
        resolves against the related model's ``slug``; mutable defaults are
        deep-copied so rows never alias the class-level dict.
        """

        changed: set[str] = set()
        for field_name, value in cls.effective_defaults().items():
            try:
                field = instance._meta.get_field(field_name)
            except FieldDoesNotExist:
                continue
            if field_name in provided or getattr(field, "attname", field_name) in provided:
                continue
            attname = getattr(field, "attname", field_name)
            before = getattr(instance, attname)
            if field.many_to_one and isinstance(value, str):
                cls._materialize_fk(instance, field, value)
            else:
                setattr(instance, field_name, copy.deepcopy(value))
            if getattr(instance, attname) != before:
                changed.add(attname)
        return changed

    @staticmethod
    def _materialize_fk(instance: models.Model, field: Any, natural_key: str) -> None:
        """Resolve a string FK default against the related model's ``slug`` and assign it."""

        related = field.related_model
        try:
            related._meta.get_field("slug")
        except FieldDoesNotExist as error:
            raise FieldDoesNotExist(
                f"{type(instance).__name__}.{field.name} impl default targets {related._meta.label}, "
                "which must declare a slug field."
            ) from error
        with system_context(reason="angee.impl.materialize_fk"):
            target = related._base_manager.filter(slug=natural_key).first()
        if target is None:
            raise ValueError(
                f"{type(instance).__name__}.{field.name} impl default references "
                f"{related._meta.label} slug {natural_key!r}, but no row exists."
            )
        setattr(instance, field.name, target)


def impl_registry(registry_setting: str) -> dict[str, str]:
    """Return the configured ``key -> dotted path`` mapping for ``registry_setting``."""

    mapping = getattr(settings, registry_setting, {}) if registry_setting else {}
    if not isinstance(mapping, Mapping):
        raise ImproperlyConfigured(f"settings.{registry_setting} must be a mapping of key to dotted path.")
    return {str(key): str(value) for key, value in mapping.items()}


def declared_impl_registries() -> tuple[str, ...]:
    """Return dotted implementation bases listed by installed addons."""

    declared = getattr(settings, "ANGEE_IMPL_REGISTRIES", ())
    if not isinstance(declared, list | tuple) or not all(isinstance(path, str) for path in declared):
        raise ImproperlyConfigured("settings.ANGEE_IMPL_REGISTRIES must be a list of dotted base classes.")
    return tuple(declared)


def declared_hooks() -> tuple[str, ...]:
    """Return hook setting names declared by installed addons."""

    declared = getattr(settings, "ANGEE_HOOKS", ())
    if not isinstance(declared, list | tuple) or not all(isinstance(name, str) for name in declared):
        raise ImproperlyConfigured("settings.ANGEE_HOOKS must be a list of hook setting names.")
    return tuple(declared)


def _hook_paths(setting: str, *, sorted_unique: bool = False) -> tuple[str, ...]:
    """Read one single or ordered multi-hook declaration without importing it."""

    configured = getattr(settings, setting, "")
    if isinstance(configured, str):
        paths = (configured,) if configured else ()
    elif isinstance(configured, list | tuple) and all(isinstance(path, str) for path in configured):
        paths = tuple(configured)
    else:
        raise ImproperlyConfigured(f"settings.{setting} must be a dotted path or a list of dotted paths.")
    if not all(paths):
        raise ImproperlyConfigured(f"settings.{setting} contains an empty hook path.")
    return tuple(sorted(set(paths))) if sorted_unique else paths


def _resolve_hook_path(setting: str, path: str) -> Callable[..., Any]:
    """Import and validate one hook path from trusted settings."""

    try:
        hook = import_string(path)
    except ImportError as error:
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} cannot be imported: {error}") from error
    if not callable(hook):
        raise ImproperlyConfigured(f"settings.{setting} hook {path!r} is not callable.")
    return hook


def resolve_hooks(setting: str, *, sorted_unique: bool = False) -> tuple[Callable[..., Any], ...]:
    """Resolve an ordered hook list; owners may request sorted unique paths."""

    return tuple(_resolve_hook_path(setting, path) for path in _hook_paths(setting, sorted_unique=sorted_unique))


def resolve_hook(setting: str) -> Callable[..., Any] | None:
    """Resolve one optional hook from a setting."""

    paths = _hook_paths(setting)
    if len(paths) > 1:
        raise ImproperlyConfigured(f"settings.{setting} must name at most one hook.")
    return _resolve_hook_path(setting, paths[0]) if paths else None


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

    registry_setting = getattr(base_class, "registry_setting")
    classes: list[type[T]] = []
    for key in sorted(impl_registry(registry_setting)):
        try:
            impl = resolve_impl_class(base_class, key)
        except (ImportError, ImproperlyConfigured) as error:
            if on_error is None:
                raise
            on_error(error)
            continue
        classes.append(impl)
    return tuple(classes)


def check_impl_registry(
    base_class: type[object],
    *,
    obj: object | None = None,
) -> list[checks.CheckMessage]:
    """Check registry declarations and offered config forms, including rowless registries.

    Empty registries are valid catalogues. Model fields and enum projections
    require entries, and consumers own any required selected-key policy.
    """

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


def impl_choices(base_class: type[object]) -> list[ImplChoice]:
    """Project pickable metadata in registry-key order, without requiring a column."""

    registry_setting = getattr(base_class, "registry_setting")
    choices: list[ImplChoice] = []
    for key in sorted(impl_registry(registry_setting)):
        impl = resolve_impl_class(base_class, key)
        if issubclass(impl, ImplBase):
            choices.append(replace(impl.choice(), key=key))
        else:
            choices.append(ImplChoice(key=key, label=key, icon="", category="", defaults={}, config_schema=None))
    return choices


def impl_choices_enum(base_class: type[object]) -> type[models.TextChoices]:
    """Return the shared native enum for the current non-empty registry key set."""

    registry_setting = getattr(base_class, "registry_setting")
    keys = tuple(sorted(impl_registry(registry_setting)))
    if not keys:
        raise ImproperlyConfigured(
            f"Implementation registry settings.{registry_setting} is empty; "
            "at least one implementation is required to build its enum."
        )
    return _impl_choices_enum(registry_setting, keys)


@cache
def _impl_choices_enum(registry_setting: str, keys: tuple[str, ...]) -> type[models.TextChoices]:
    """Share enum identity across projections while settings changes select new keys."""

    core = registry_setting.removeprefix("ANGEE_").removesuffix("_CLASSES")
    camel = "".join(part.capitalize() for part in core.split("_") if part)
    members = [(key.upper(), (key, key)) for key in keys]
    return cast("type[models.TextChoices]", models.TextChoices(f"{camel or 'Impl'}Impl", members))


class ImplClassField(TextChoicesField):
    """A column naming a non-model implementation class by a short key.

    The base names the Django setting that maps keys to dotted import paths.
    Addons contribute impls into that setting through autoconfig, making
    the key set closed at composition time. The field renders as a
    ``TextChoices`` enum and resolves only configured, trusted paths.
    """

    def __init__(
        self,
        base_class: type[object] | None = None,
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

    def deconstruct(self) -> tuple[str | None, str, list[Any], dict[str, Any]]:
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

    def resolve_class(self, key: Any) -> type:
        """Return the impl class the configured mapping binds to ``key``."""

        return resolve_impl_class(cast(type, self.base_class), self.key_for(key))

    def registered_keys(self) -> tuple[str, ...]:
        """Return this field's configured implementation keys in deterministic order."""

        return tuple(sorted(impl_registry(self.registry_setting)))

    def resolve_for(self, instance: models.Model) -> type:
        """Return the impl class selected by this field on ``instance``."""

        return self.resolve_class(getattr(instance, self.attname))

    def key_for(self, value: Any) -> str:
        """Return the canonical registry key for a stored/input enum-ish value."""

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


class ImplDefaultsMixin(models.Model):
    """Materialise impl defaults on create for every ``ImplClassField`` on the model.

    The backend safety net behind the form-level prefill: a row created without a
    form (API, resource seed) still gets the chosen impl's defaults — for the fields
    the caller did not supply. Form-created rows pass their (possibly edited) values,
    so the impl never overrides them, even when a value equals the model default.
    """

    class Meta:
        """Abstract: contributes the create-time default seeding only."""

        abstract = True

    @classmethod
    def from_db(cls, db: str, field_names: list[str], values: list[Any]) -> ImplDefaultsMixin:
        """Remember loaded impl keys so every persisted write ingress enforces immutability."""

        instance = super().from_db(db, field_names, values)
        loaded = dict(zip(field_names, values, strict=True))
        instance._loaded_impl_keys = {
            field.attname: loaded[field.attname]
            for field in instance._meta.get_fields()
            if isinstance(field, ImplClassField) and field.attname in loaded
        }
        return instance

    def refresh_from_db(
        self, using: str | None = None, fields: Iterable[str] | None = None,
        from_queryset: models.QuerySet[Any] | None = None,
    ) -> None:
        """Keep the immutable-key snapshot coherent when Django reloads those fields."""

        refreshed = None if fields is None else set(fields)
        super().refresh_from_db(using=using, fields=refreshed, from_queryset=from_queryset)
        loaded = dict(getattr(self, "_loaded_impl_keys", {}))
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or not field.create_only:
                continue
            if refreshed is not None and field.name not in refreshed and field.attname not in refreshed:
                continue
            if field.attname in self.__dict__:
                loaded[field.attname] = self.__dict__[field.attname]
        self._loaded_impl_keys = loaded

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Record the caller-supplied field names so create-time seeding skips them."""

        self._impl_provided_fields = frozenset(kwargs)
        super().__init__(*args, **kwargs)

    def mark_impl_provided_fields(self, field_names: Iterable[str]) -> None:
        """Record fields assigned after construction by a structured write ingress."""

        provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())
        self._impl_provided_fields = provided | frozenset(field_names)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Seed impl defaults for unsupplied fields on first insert, then persist."""

        adding = self._state.adding
        if adding:
            provided: frozenset[str] = getattr(self, "_impl_provided_fields", frozenset())

            for field in self._meta.get_fields():
                if not isinstance(field, ImplClassField):
                    continue
                key = getattr(self, field.attname, None)
                if not key:
                    continue
                impl = field.resolve_class(key)
                if isinstance(impl, type) and issubclass(impl, ImplBase):
                    impl.materialize(self, provided=provided)
        update_fields = kwargs.get("update_fields")
        self.validate_impl_keys(update_fields=update_fields)
        self.validate_impl_configs(update_fields=update_fields)
        super().save(*args, **kwargs)
        loaded = dict(getattr(self, "_loaded_impl_keys", {}))
        updated = None if update_fields is None else set(update_fields)
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or field.attname not in self.__dict__:
                continue
            if adding or updated is None or field.name in updated or field.attname in updated:
                loaded[field.attname] = self.__dict__[field.attname]
        self._loaded_impl_keys = loaded

    def validate_impl_keys(self, *, update_fields: Any = None) -> None:
        """Reject persisted implementation switches at the shared model boundary."""

        if self._state.adding and self.pk is None:
            return
        updated = None if update_fields is None else set(update_fields)
        loaded = getattr(self, "_loaded_impl_keys", {})
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or not field.create_only:
                continue
            # A deferred selector omitted from the write keeps Django's loaded-field save semantics.
            if not self._state.adding and updated is None and field.attname not in self.__dict__:
                continue
            if field.attname not in loaded:
                if updated is not None and field.name not in updated and field.attname not in updated:
                    continue
                with system_context(reason="base.impl.validate_stored_key"):
                    stored_row = type(self)._base_manager.filter(pk=self.pk).values_list(field.attname).first()
                if stored_row is None and self._state.adding:
                    continue
                if stored_row is None:
                    raise ValidationError({field.name: "Stored implementation selection could not be verified."})
                stored = stored_row[0]
                loaded[field.attname] = stored
            if updated is not None and field.name not in updated and field.attname not in updated:
                continue
            if getattr(self, field.attname) != loaded[field.attname]:
                raise ValidationError({field.name: "Implementation selection is create-only."})

    def apply_config_patch(self, patch: Mapping[str, Any]) -> set[str]:
        """Merge top-level config keys, removing explicit ``None`` values.

        Return changed model field names for ``save(update_fields=...)``. Saving
        still validates and normalizes the merged config through its impl.
        """

        if not isinstance(patch, Mapping):
            raise ValidationError({"config": "Config patch must be an object."})
        current = getattr(self, "config")
        merged = dict(current)
        for key, value in patch.items():
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        if merged == current:
            return set()
        setattr(self, "config", merged)
        return {"config"}

    def validate_impl_configs(self, *, update_fields: Any = None) -> None:
        """Validate declared config models on save, leaving untyped row config alone."""

        if not self._state.adding and update_fields is not None and "config" not in update_fields:
            return
        # Do not load and rewrite deferred config on an unrelated model save.
        if "config" not in self.__dict__ and (update_fields is None or "config" not in update_fields):
            return
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField):
                continue
            key = getattr(self, field.attname, None)
            if not key:
                continue
            impl = field.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase) and impl.config_model is not None:
                normalized = impl.normalize_config(self.config)
                setattr(self, "config", normalized)

    def set_impl_key(self, field_name: str, value: Any, *, default: str | None = None) -> bool:
        """Assign an impl key and return whether the stored key changed."""

        field = type(self).impl_field(field_name)
        key = type(self).impl_key_for(field_name, value, default=default)
        changed = key != getattr(self, field.attname)
        if changed and field.create_only and not self._state.adding:
            raise ValidationError({field.name: "Implementation selection is create-only."})
        setattr(self, field.attname, key)
        return changed

    def materialize_impl_defaults(
        self,
        field_name: str,
        *,
        provided: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Apply the selected impl's defaults for one impl field."""

        field = type(self).impl_field(field_name)
        key = getattr(self, field.attname, None)
        if not key:
            return set()
        impl = field.resolve_class(key)
        if isinstance(impl, type) and issubclass(impl, ImplBase):
            return impl.materialize(self, provided=provided | {field.name, field.attname})
        return set()
