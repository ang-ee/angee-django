"""Registry-backed implementation selection for Angee models and settings.

This module is the single owner of the impl mechanism: the model field that
stores a selected key, the metadata base classes impls subclass, and the
settings-backed registry resolver shared by row-owned and row-less selectors.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, NoReturn, cast, get_args

from django.conf import settings
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models, router
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rebac import system_context

from angee.base.fields import enum_member_for

__all__ = [
    "ImplBase",
    "ImplChoice",
    "ImplClassField",
    "ImplDefaultsMixin",
    "impl_registry",
    "resolve_impl_class",
    "model_config_form_spec",
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


_SCHEMA_COMMON_KEYS = frozenset({"title", "description", "default"})


class _ConfigFormSpecProjector:
    """Own the bounded translation from one Pydantic schema into FormSpec."""

    def __init__(self, model: type[BaseModel], *, owner: str) -> None:
        self.owner = owner
        self._validate_aliases(model, path="config", seen=frozenset())
        self.schema = model.model_json_schema(by_alias=False)
        definitions = self.schema.pop("$defs", {})
        if not isinstance(definitions, dict):
            self._unsupported("config", "$defs")
        self.definitions = definitions

    def form_spec(self) -> dict[str, Any]:
        projected = self._project(self.schema, path="config", refs=())
        if projected.get("type") != "object":
            self._unsupported("config", "root type")
        for key in ("label", "description", "defaultValue", "widget"):
            projected.pop(key, None)
        return projected

    def _project(self, schema: Any, *, path: str, refs: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(schema, dict):
            self._unsupported(path, "non-object schema")
        if schema.get("widget") == "json":
            schema_type = schema.get("type")
            return self._metadata(
                {"type": schema_type if schema_type in {"object", "array"} else "any", "widget": "json"},
                schema,
            )
        if "$ref" in schema:
            self._reject_keywords(schema, _SCHEMA_COMMON_KEYS | {"$ref"}, path)
            reference = schema["$ref"]
            prefix = "#/$defs/"
            if not isinstance(reference, str) or not reference.startswith(prefix):
                self._unsupported(path, f"reference {reference!r}")
            name = reference.removeprefix(prefix)
            if name in refs:
                self._unsupported(path, f"recursive reference {reference!r}")
            projected = self._project(self.definitions.get(name), path=path, refs=(*refs, name))
            projected.pop("label", None)
            return self._metadata(projected, schema)

        if "anyOf" in schema:
            self._reject_keywords(schema, _SCHEMA_COMMON_KEYS | {"anyOf"}, path)
            choices = schema["anyOf"]
            if not isinstance(choices, list) or len(choices) != 2:
                self._unsupported(path, "union")
            concrete = [choice for choice in choices if choice != {"type": "null"}]
            if len(concrete) != 1:
                self._unsupported(path, "union")
            projected = self._project(concrete[0], path=path, refs=refs)
            projected["nullable"] = True
            return self._metadata(projected, schema)

        schema_type = schema.get("type")
        if schema_type in {"string", "integer", "number", "boolean"}:
            constraints = (
                {"minLength", "maxLength"}
                if schema_type == "string"
                else {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
                if schema_type in {"integer", "number"}
                else set()
            )
            self._reject_keywords(
                schema,
                _SCHEMA_COMMON_KEYS | {"type", "enum", "const", "format"} | constraints,
                path,
            )
            scalar_projection: dict[str, Any] = {"type": schema_type}
            if "format" in schema:
                if schema_type != "string" or schema["format"] not in {"date", "date-time"}:
                    self._unsupported(path, f"format {schema['format']!r}")
                scalar_projection["widget"] = "date" if schema["format"] == "date" else "datetime"
            for constraint in constraints:
                if constraint in schema:
                    scalar_projection[constraint] = schema[constraint]
            if schema_type == "integer":
                if "exclusiveMinimum" in scalar_projection:
                    exclusive_minimum = math.floor(scalar_projection.pop("exclusiveMinimum")) + 1
                    scalar_projection["minimum"] = max(
                        scalar_projection.get("minimum", exclusive_minimum), exclusive_minimum
                    )
                if "exclusiveMaximum" in scalar_projection:
                    exclusive_maximum = math.ceil(scalar_projection.pop("exclusiveMaximum")) - 1
                    scalar_projection["maximum"] = min(
                        scalar_projection.get("maximum", exclusive_maximum), exclusive_maximum
                    )
            elif "exclusiveMinimum" in scalar_projection or "exclusiveMaximum" in scalar_projection:
                self._unsupported(path, "exclusive numeric bound")
            enum = schema.get("enum")
            if "const" in schema:
                enum = [schema["const"]]
                scalar_projection["const"] = copy.deepcopy(schema["const"])
            if enum is not None:
                if not isinstance(enum, list) or not enum or not all(isinstance(value, str) for value in enum):
                    self._unsupported(path, "non-string enum")
                scalar_projection["enum"] = copy.deepcopy(enum)
            return self._metadata(scalar_projection, schema)

        if schema_type == "object":
            self._reject_keywords(
                schema,
                _SCHEMA_COMMON_KEYS | {"type", "properties", "required", "additionalProperties"},
                path,
            )
            if schema.get("additionalProperties", False) not in (False, True, None):
                self._unsupported(path, "mapping/additionalProperties")
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if (
                not isinstance(properties, dict)
                or not isinstance(required, list)
                or not all(isinstance(name, str) for name in required)
            ):
                self._unsupported(path, "object properties")
            if schema.get("additionalProperties") is True and path != "config":
                self._unsupported(path, "free-form mapping")
            projected_properties = {}
            for name, field in properties.items():
                projected = self._project(field, path=f"{path}.{name}", refs=refs)
                projected.setdefault("label", name.replace("_", " ").title())
                if name not in required:
                    projected["omittable"] = True
                else:
                    projected["presenceRequired"] = True
                projected_properties[name] = projected
            return self._metadata(
                {
                    "type": "object",
                    "widget": "object",
                    "properties": projected_properties,
                    "required": list(required),
                },
                schema,
            )

        if schema_type == "array":
            self._reject_keywords(schema, _SCHEMA_COMMON_KEYS | {"type", "items", "minItems", "maxItems"}, path)
            if "items" not in schema:
                self._unsupported(path, "array without items")
            return self._metadata(
                {
                    "type": "array",
                    "widget": "list",
                    "items": self._project(schema["items"], path=f"{path}[]", refs=refs),
                    **({"minItems": schema["minItems"]} if "minItems" in schema else {}),
                    **({"maxItems": schema["maxItems"]} if "maxItems" in schema else {}),
                },
                schema,
            )

        self._unsupported(path, f"type {schema_type!r}")

    def _metadata(self, projected: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        result = dict(projected)
        if title := schema.get("title"):
            result["label"] = title
        if description := schema.get("description"):
            result["description"] = description
        if "default" in schema:
            result["defaultValue"] = copy.deepcopy(schema["default"])
        return result

    def _reject_keywords(self, schema: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
        unsupported = sorted(set(schema) - set(allowed))
        if unsupported:
            self._unsupported(path, f"keywords {', '.join(unsupported)}")

    def _validate_aliases(self, model: type[BaseModel], *, path: str, seen: frozenset[type[BaseModel]]) -> None:
        if model in seen:
            return
        for name, field in model.model_fields.items():
            field_path = f"{path}.{name}"
            if field.alias is not None or field.validation_alias is not None or field.serialization_alias is not None:
                raise ImproperlyConfigured(f"{self.owner}.config_model field {field_path!r} cannot declare aliases.")
            for nested in _pydantic_models_in(field.annotation):
                self._validate_aliases(nested, path=field_path, seen=seen | {model})

    def _unsupported(self, path: str, detail: str) -> NoReturn:
        raise ImproperlyConfigured(f"{self.owner}.config_model field {path!r} uses unsupported schema: {detail}.")


def _pydantic_models_in(annotation: Any) -> tuple[type[BaseModel], ...]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    return tuple(model for argument in get_args(annotation) for model in _pydantic_models_in(argument))


def model_config_form_spec(model: type[BaseModel], *, owner: str) -> dict[str, Any]:
    """Project a Pydantic model through the shared bounded FormSpec owner.

    Declared properties become structured fields. Extra root properties are not
    projected; callers that allow them must retain their existing raw JSON owner.
    """

    return _ConfigFormSpecProjector(model, owner=owner).form_spec()


class ImplBase:
    """Base for an implementation selectable by an ``ImplClassField`` key.

    Subclasses declare class-level ``key``/``label``/``icon``/``category`` and a
    ``defaults`` mapping of model-field values to seed. Behaviour lives on the
    domain subclass (e.g. ``IntegrationImpl``, ``OAuthProviderType``).
    """

    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    icon: ClassVar[str] = ""
    category: ClassVar[str] = ""
    defaults: ClassVar[dict[str, Any]] = {}
    config_model: ClassVar[type[BaseModel] | None] = None

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
        """Return non-empty config suggestions from the authoritative typed declaration."""

        if cls.config_model is None:
            return {}
        spec = cls.config_form_spec()
        assert spec is not None
        return {
            name: copy.deepcopy(field["defaultValue"])
            for name, field in spec["properties"].items()
            if field.get("defaultValue") not in (None, "")
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
    def normalize_config(cls, value: Any) -> dict[str, Any]:
        """Validate and normalize adapter-owned JSON through its optional model."""

        if cls.config_model is None:
            return cast(dict[str, Any], value)
        try:
            validated = cls.config_model.model_validate(value)
        except PydanticValidationError as error:
            messages: dict[str, list[str]] = {}
            for issue in error.errors(include_url=False, include_context=False, include_input=False):
                location = ".".join(str(part) for part in issue["loc"])
                path = f"config.{location}" if location else "config"
                messages.setdefault(path, []).append(str(issue["msg"]))
            raise ValidationError(messages) from None
        return validated.model_dump(mode="json")

    @classmethod
    def config_form_spec(cls) -> dict[str, Any] | None:
        """Translate Pydantic's supported JSON Schema subset into FormSpec."""

        if cls.config_model is None:
            return None
        return model_config_form_spec(cls.config_model, owner=cls.__name__)

    @classmethod
    def materialize(cls, instance: models.Model, *, provided: frozenset[str] = frozenset()) -> set[str]:
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


def resolve_impl_class(registry_setting: str, key: str, base_class: type) -> type:
    """Return the impl class ``registry_setting`` binds to ``key``.

    The dotted path comes from composed, trusted settings and is checked against
    ``base_class`` before returning.
    """

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
    return impl


class ImplClassField(TextChoicesField):
    """A column naming a non-model implementation class by a short key.

    ``registry_setting`` names the Django setting that maps keys to dotted import
    paths. Addons contribute impls into that setting through autoconfig, making
    the key set closed at composition time. The field renders as a
    ``TextChoices`` enum and resolves only configured, trusted paths.
    """

    def __init__(
        self,
        *,
        base_class: type | None = None,
        registry_setting: str = "",
        create_only: bool = False,
        **kwargs: Any,
    ) -> None:
        """Bind the implementation base and build the enum from the registry keys."""

        if base_class is not None and not isinstance(base_class, type):
            raise ImproperlyConfigured("ImplClassField base_class must be a type.")
        self.base_class = base_class
        self.registry_setting = registry_setting
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
        """Validate the declaration and every configured impl path."""

        errors = super().check(**kwargs)
        if not isinstance(self.base_class, type):
            errors.append(
                checks.Error(
                    "ImplClassField requires a base_class type.",
                    hint="Pass base_class=... naming the implementation base.",
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
            for key, dotted in self._registry().items():
                try:
                    impl = import_string(dotted)
                except ImportError as error:
                    errors.append(
                        checks.Error(
                            f"settings.{self.registry_setting}[{key!r}] = {dotted!r} does not import: {error}",
                            obj=self,
                            id="angee.E003",
                        )
                    )
                    continue
                if not (isinstance(impl, type) and issubclass(impl, self.base_class)):
                    errors.append(
                        checks.Error(
                            f"settings.{self.registry_setting}[{key!r}] = {dotted!r} "
                            f"is not a {self.base_class.__name__} subclass.",
                            obj=self,
                            id="angee.E004",
                        )
                    )
                    continue
                if issubclass(impl, ImplBase):
                    try:
                        impl.config_form_spec()
                    except ImproperlyConfigured as error:
                        errors.append(checks.Error(str(error), obj=self, id="angee.E005"))
        return errors

    def resolve_class(self, key: Any) -> type:
        """Return the impl class the configured mapping binds to ``key``."""

        return resolve_impl_class(self.registry_setting, self.key_for(key), cast(type, self.base_class))

    def registered_keys(self) -> tuple[str, ...]:
        """Return this field's configured implementation keys in deterministic order."""

        return tuple(sorted(self._registry()))

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

        keys = sorted(self._registry())
        if not keys:
            if self.base_class is None:
                default = self._historical_default
                if isinstance(default, str) and default:
                    members = [(default.upper(), (default, default))]
                    return cast("type[models.TextChoices]", models.TextChoices(self._enum_name(), members))
            raise ImproperlyConfigured(
                f"ImplClassField registry settings.{self.registry_setting} is empty; an addon must "
                "contribute at least one impl (e.g. a noop/null-object default) before the field is built."
            )
        members = [(key.upper(), (key, key)) for key in keys]
        return cast("type[models.TextChoices]", models.TextChoices(self._enum_name(), members))

    def _enum_name(self) -> str:
        """Return a stable PascalCase GraphQL enum name derived from ``registry_setting``."""

        core = self.registry_setting.removeprefix("ANGEE_").removesuffix("_CLASSES")
        camel = "".join(part.capitalize() for part in core.split("_") if part)
        return f"{camel or 'Impl'}Impl"

    def impl_choices(self) -> list[ImplChoice]:
        """Return pickable choices for the registry in deterministic key order."""

        choices: list[ImplChoice] = []
        for key in sorted(self._registry()):
            impl = self.resolve_class(key)
            if isinstance(impl, type) and issubclass(impl, ImplBase):
                choice = impl.choice()
                choices.append(
                    ImplChoice(
                        key=key,
                        label=choice.label,
                        icon=choice.icon,
                        category=choice.category,
                        defaults=choice.defaults,
                        config_schema=choice.config_schema,
                    )
                )
            else:
                choices.append(ImplChoice(key=key, label=key, icon="", category="", defaults={}, config_schema=None))
        return choices

    def _registry(self) -> dict[str, str]:
        """Return the configured ``key -> dotted path`` mapping for this field."""

        return impl_registry(self.registry_setting)


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

    def refresh_from_db(self, using: str | None = None, fields: Any = None, **kwargs: Any) -> None:
        """Keep the immutable-key snapshot coherent when Django reloads those fields."""

        super().refresh_from_db(using=using, fields=fields, **kwargs)
        refreshed = None if fields is None else set(fields)
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
        self.validate_impl_keys(update_fields=update_fields, using=kwargs.get("using"))
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

    def validate_impl_keys(self, *, update_fields: Any = None, using: str | None = None) -> None:
        """Reject persisted implementation switches at the shared model boundary."""

        if self._state.adding and self.pk is None:
            return
        updated = None if update_fields is None else set(update_fields)
        loaded = getattr(self, "_loaded_impl_keys", {})
        for field in self._meta.get_fields():
            if not isinstance(field, ImplClassField) or not field.create_only:
                continue
            if field.attname not in loaded:
                if updated is not None and field.name not in updated and field.attname not in updated:
                    continue
                alias = using or router.db_for_write(type(self), instance=self)
                with system_context(reason="base.impl.validate_stored_key"):
                    stored_row = (
                        type(self)._base_manager.using(alias).filter(pk=self.pk).values_list(field.attname).first()
                    )
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
        """Validate every declared adapter config before any model save ingress."""

        if not hasattr(self, "config"):
            return
        if not self._state.adding and update_fields is not None and "config" not in update_fields:
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
