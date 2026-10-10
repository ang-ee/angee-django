"""Registry-backed implementation selection for Angee models and settings.

This module is the single owner of the impl mechanism: the model field that
stores a selected key, the metadata base classes impls subclass, and the
settings-backed registry resolver shared by row-owned and row-less selectors.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from functools import cache
from typing import Any, ClassVar, NoReturn, cast, get_args

from django.conf import settings
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.db import models
from django.utils.module_loading import import_string
from django_choices_field import TextChoicesField
from pydantic import BaseModel
from rebac import system_context

from angee.base.fields import enum_member_for
from angee.base.jsonschema import (
    LocalSchemaReferences,
    schema_nodes,
    validation_issues,
    validator,
)
from angee.base.validation import validate_value

__all__ = [
    "FORM_SCHEMA_ANNOTATIONS",
    "FORM_SPEC_RELATION_VALIDATOR",
    "check_form_annotations",
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


_SCHEMA_COMMON_KEYS = frozenset({"title", "description", "default", "widget", "relation"})
_POLICY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_FILTER_OPERATORS = (
    "eq",
    "ne",
    "eqs",
    "nes",
    "lt",
    "gt",
    "lte",
    "gte",
    "in",
    "nin",
    "ina",
    "nina",
    "contains",
    "ncontains",
    "containss",
    "ncontainss",
    "between",
    "nbetween",
    "null",
    "nnull",
    "startswith",
    "nstartswith",
    "startswiths",
    "nstartswiths",
    "endswith",
    "nendswith",
    "endswiths",
    "nendswiths",
)
_FORM_SPEC_RELATION_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "json": {
            "oneOf": [
                {"type": "null"},
                {"type": "string"},
                {"type": "number"},
                {"type": "boolean"},
                {"type": "array", "items": {"$ref": "#/$defs/json"}},
                {"type": "object", "additionalProperties": {"$ref": "#/$defs/json"}},
            ]
        },
        "filter": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operator", "value"],
                    "properties": {
                        "operator": {"enum": ["and", "or"]},
                        "key": {"type": "string"},
                        "value": {"type": "array", "items": {"$ref": "#/$defs/filter"}},
                    },
                },
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["operator", "field"],
                    "properties": {
                        "operator": {"enum": list(_FILTER_OPERATORS)},
                        "field": {"type": "string", "minLength": 1},
                        "value": {"$ref": "#/$defs/json"},
                    },
                },
            ]
        },
    },
    "type": "object",
    "additionalProperties": False,
    "required": ["resource"],
    "properties": {
        "resource": {"type": "string", "minLength": 1},
        "permission": {"type": "string", "pattern": _POLICY_IDENTIFIER.pattern},
        "labelField": {"type": "string", "minLength": 1},
        "filters": {"type": "array", "items": {"$ref": "#/$defs/filter"}},
        "create": {
            "type": "object",
            "additionalProperties": False,
            "required": ["resource"],
            "properties": {
                "resource": {"type": "string", "minLength": 1},
                "defaultValues": {"type": "object", "additionalProperties": {"$ref": "#/$defs/json"}},
            },
        },
    },
}
FORM_SPEC_RELATION_VALIDATOR = validator(_FORM_SPEC_RELATION_SCHEMA)
"""Native validator for the shared FormSpec relation metadata contract."""

FORM_SCHEMA_ANNOTATIONS = frozenset(
    {"title", "description", "widget", "relation", "options", "readOnly"}
)
"""Presentation annotations shared by config forms and workflow input schemas."""

def _nullable_form_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """The one concrete branch of the bounded nullable FormSpec union."""
    choices = schema.get("anyOf")
    if isinstance(choices, list) and len(choices) == 2 and all(isinstance(choice, dict) for choice in choices):
        concrete = [choice for choice in choices if choice != {"type": "null"}]
        if len(concrete) == 1:
            return concrete[0]
    return None


def check_form_annotations(schema: dict[str, Any]) -> None:
    """Validate shared form annotations and their declared field values."""
    for name in ("title", "description", "widget"):
        if name in schema and not isinstance(schema[name], str):
            raise ValidationError(f"Form annotation {name} must be a string.")
    if "widget" in schema and not schema["widget"]:
        raise ValidationError("Form annotation widget must not be empty.")
    if "readOnly" in schema and not isinstance(schema["readOnly"], bool):
        raise ValidationError("Form annotation readOnly must be a boolean.")
    if "relation" in schema:
        if (_nullable_form_schema(schema) or schema).get("type") != "string":
            raise ValidationError("relation on a non-string field")
        widget = schema.get("widget", "many2one")
        if widget != "many2one" and "." not in widget:
            raise ValidationError("relation with a non-relation widget")
        error = next(FORM_SPEC_RELATION_VALIDATOR.iter_errors(schema["relation"]), None)
        if error is not None:
            location = ".".join(str(part) for part in error.absolute_path)
            raise ValidationError(f"invalid relation{f' at {location}' if location else ''}: {error.message}")
    if "options" in schema:
        options = schema["options"]
        if not isinstance(options, list) or not options or any(
            not isinstance(option, dict) or set(option) != {"value", "label"}
            or not isinstance(option["label"], str) or validation_issues(schema, option["value"])
            for option in options
        ):
            raise ValidationError("Field options require valid values and labels.")


class _UnsupportedConfigSchema(ImproperlyConfigured):
    """The projector rejected a shape outside the bounded form vocabulary."""


class _ConfigFormSpecProjector:
    """Project bounded FormSpec shapes, resolving root-local refs through referencing."""

    def __init__(self, model: type[BaseModel], *, owner: str, json_fields: bool = False) -> None:
        self.owner = owner
        self.json_fields = json_fields
        _validate_config_aliases(model, owner=owner)
        schema = model.model_json_schema(by_alias=True)
        self.references = LocalSchemaReferences(schema)
        for node in schema_nodes(schema):
            if "relation" in node and "$ref" in node:
                target = self.references.resolve(node["$ref"])
                if isinstance(target, dict):
                    node = {**target, **node}
            try:
                check_form_annotations(node)
            except ValidationError as error:
                raise ImproperlyConfigured(f"{owner}: invalid form annotation: {error}") from error
        if not isinstance(schema.get("$defs", {}), dict):
            self._unsupported("config", "$defs")
        self.schema = {key: value for key, value in schema.items() if key != "$defs"}

    def form_spec(self) -> dict[str, Any]:
        projected = self._project(self.schema, path="config", refs=())
        if projected.get("type") != "object":
            self._unsupported("config", "root type")
        for key in ("label", "description", "defaultValue", "widget"):
            projected.pop(key, None)
        return projected

    def _project(self, schema: Any, *, path: str, refs: tuple[str, ...]) -> dict[str, Any]:
        try:
            if isinstance(schema, dict) and schema.get("widget") == "json":
                return self._json_field(schema)
            return self._project_supported(schema, path=path, refs=refs)
        except _UnsupportedConfigSchema:
            if not self.json_fields or path == "config":
                raise
            return self._json_field(schema if isinstance(schema, dict) else {})

    def _json_field(self, schema: dict[str, Any]) -> dict[str, Any]:
        projected = {"type": schema.get("type") if schema.get("type") in {"object", "array"} else "any"}
        return {**self._metadata(projected, schema), "widget": "json"}

    def _project_supported(self, schema: Any, *, path: str, refs: tuple[str, ...]) -> dict[str, Any]:
        if not isinstance(schema, dict):
            self._unsupported(path, "non-object schema")
        if "$ref" in schema:
            self._reject_keywords(schema, _SCHEMA_COMMON_KEYS | {"$ref"}, path)
            reference = schema["$ref"]
            target = self.references.resolve(reference)
            if target is None:
                self._unsupported(path, f"reference {reference!r}")
            if reference in refs:
                self._unsupported(path, f"recursive reference {reference!r}")
            projected = self._project(target, path=path, refs=(*refs, reference))
            projected.pop("label", None)
            return self._metadata(projected, schema)

        if "anyOf" in schema:
            self._reject_keywords(schema, _SCHEMA_COMMON_KEYS | {"anyOf"}, path)
            concrete = _nullable_form_schema(schema)
            if concrete is None:
                self._unsupported(path, "union")
            projected = self._project(concrete, path=path, refs=refs)
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
        if "widget" in schema:
            result["widget"] = schema["widget"]
        if "relation" in schema:
            result["relation"] = copy.deepcopy(schema["relation"])
        return result

    def _reject_keywords(self, schema: dict[str, Any], allowed: set[str] | frozenset[str], path: str) -> None:
        unsupported = sorted(set(schema) - set(allowed))
        if unsupported:
            self._unsupported(path, f"keywords {', '.join(unsupported)}")

    def _unsupported(self, path: str, detail: str) -> NoReturn:
        raise _UnsupportedConfigSchema(f"{self.owner}.config_model field {path!r} uses unsupported schema: {detail}.")


def _pydantic_models_in(annotation: Any) -> tuple[type[BaseModel], ...]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    return tuple(model for argument in get_args(annotation) for model in _pydantic_models_in(argument))


def _validate_config_aliases(
    model: type[BaseModel], *, owner: str, path: str = "config", seen: frozenset[type[BaseModel]] = frozenset()
) -> None:
    """Keep one unambiguous input/output identity for every declared config field."""

    if model in seen:
        return
    wire_names = [field.alias or name for name, field in model.model_fields.items()]
    duplicates = sorted({name for name in wire_names if wire_names.count(name) > 1})
    if duplicates:
        raise ImproperlyConfigured(
            f"{owner}.config_model field {path!r} has colliding wire names: {', '.join(duplicates)}."
        )
    for name, field in model.model_fields.items():
        field_path = f"{path}.{name}"
        alias = field.alias
        if (alias is None and (field.validation_alias is not None or field.serialization_alias is not None)) or (
            alias is not None
            and (not isinstance(alias, str) or field.validation_alias != alias or field.serialization_alias != alias)
        ):
            raise ImproperlyConfigured(
                f"{owner}.config_model field {field_path!r} must use one string alias for validation and serialization."
            )
        for nested in _pydantic_models_in(field.annotation):
            _validate_config_aliases(nested, owner=owner, path=field_path, seen=seen | {model})


def model_config_form_spec(model: type[BaseModel], *, owner: str, json_fields: bool = False) -> dict[str, Any]:
    """Project a Pydantic model through the shared bounded FormSpec owner.

    Declared properties become structured fields. Extra root properties are not
    projected; callers that allow them must retain their existing raw JSON owner.
    ``json_fields`` renders unstructured mappings, unions, recursive values and
    unsupported scalar formats through the existing JSON widget. Typed parsing
    remains the validation owner; bounded objects still expose structured fields.
    """

    return _ConfigFormSpecProjector(model, owner=owner, json_fields=json_fields).form_spec()


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
