"""Project typed Pydantic declarations into the transport-neutral FormSpec contract."""

from __future__ import annotations

import copy
import math
import re
from typing import Any, NoReturn, get_args

from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel

from angee.jsonschema import (
    LocalSchemaReferences,
    schema_nodes,
    validation_issues,
    validator,
)

__all__ = [
    "FORM_SCHEMA_ANNOTATIONS",
    "FORM_SPEC_RELATION_VALIDATOR",
    "check_form_annotations",
    "model_config_form_spec",
]

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
