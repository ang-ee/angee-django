"""Canonical FormSpec validation, typed config projection and relation positions."""
from __future__ import annotations

import copy
import json
import math
from collections.abc import Iterator
from functools import cache
from importlib.resources import files
from typing import Any, NoReturn

from django.core.exceptions import ImproperlyConfigured, ValidationError
from jsonschema import Draft202012Validator
from referencing.jsonschema import DRAFT202012

from angee.base.schemas.references import SCHEMA_ANNOTATIONS, LocalSchemaReferences, schema_intersection, schema_nodes
from angee.base.validators import check_schema, validate, validation_issues, validator
from angee.serialization import canonical_json

_SCHEMA_COMMON_KEYS = frozenset({"title", "description", "default", "widget", "relation"})

@cache
def form_spec_schema() -> dict[str, Any]:
    """Load the owning package's canonical Python and browser contract."""
    return json.loads(files(__package__).joinpath("form_spec.schema.json").read_text(encoding="utf-8"))

@cache
def _form_validator(definition: str = "") -> Draft202012Validator:
    schema = form_spec_schema()
    if definition:
        schema = {"$defs": schema["$defs"], "$ref": f"#/$defs/{definition}"}
    return validator(schema)

def validate_form_spec(value: Any) -> None:
    """Validate the complete emitted vocabulary through the canonical contract."""
    try:
        canonical_json(value)
    except (TypeError, ValueError) as error:
        raise ValidationError("FormSpec must contain finite JSON values.") from error
    validate(_form_validator(), value)

def _nullable_form_schema(schema: dict[str, Any]) -> dict[str, Any] | None:
    """The one concrete branch of the bounded nullable FormSpec union."""
    choices = schema.get("anyOf")
    if isinstance(choices, list) and len(choices) == 2 and all(isinstance(choice, dict) for choice in choices):
        concrete = [choice for choice in choices if choice != {"type": "null"}]
        if len(concrete) == 1:
            return concrete[0]
    return None


def check_form_annotations(schema: dict[str, Any]) -> None:
    """Validate schema annotations with their canonical FormSpec declarations."""
    validate(_form_validator("Annotations"), {key: value for key, value in schema.items() if key != "relation"})
    if "relation" in schema:
        if (_nullable_form_schema(schema) or schema).get("type") != "string":
            raise ValidationError("relation on a non-string field")
        widget = schema.get("widget", "many2one")
        if widget != "many2one" and "." not in widget:
            raise ValidationError("relation with a non-relation widget")
        error = next(_form_validator("Relation").iter_errors(schema["relation"]), None)
        if error is not None:
            location = ".".join(str(part) for part in error.absolute_path)
            raise ValidationError(f"invalid relation{f' at {location}' if location else ''}: {error.message}")
    if "options" in schema:
        if any(validation_issues(schema, option["value"]) for option in schema["options"]):
            raise ValidationError("Field options require valid values and labels.")

class _UnsupportedConfigSchema(ImproperlyConfigured):
    """The projector rejected a shape outside the bounded form vocabulary."""


class _ConfigFormSpecProjector:
    """Project bounded FormSpec shapes, resolving root-local refs through referencing."""

    def __init__(self, schema: dict[str, Any], *, owner: str, json_fields: bool = False) -> None:
        self.owner = owner
        self.json_fields = json_fields
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
        validate_form_spec(projected)
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


def config_form_spec(schema: dict[str, Any], *, owner: str, json_fields: bool = False) -> dict[str, Any]:
    """Project a Pydantic model through the shared bounded FormSpec owner.

    Declared properties become structured fields. Extra root properties are not
    projected; callers that allow them must retain their existing raw JSON owner.
    ``json_fields`` renders unstructured mappings, unions, recursive values and
    unsupported scalar formats through the existing JSON widget. Typed parsing
    remains the validation owner; bounded objects still expose structured fields.
    """

    return _ConfigFormSpecProjector(schema, owner=owner, json_fields=json_fields).form_spec()


def materialize_schema(
    schema: dict[str, Any], *, annotations: frozenset[str] = SCHEMA_ANNOTATIONS,
) -> dict[str, Any]:
    """Copy a finite schema with local references inlined and unused definitions removed.

    Assertions beside a reference remain constraints; local annotations take
    precedence for presentation. Recursive references cannot be materialized.
    Literal JSON values and extension metadata remain untouched.
    """
    check_schema(schema)
    if any("$dynamicRef" in node or "$dynamicAnchor" in node for node in schema_nodes(schema)):
        raise ValidationError("Materialized schemas do not support dynamic scope.")
    references = LocalSchemaReferences(schema)

    def expand(current: Any, active: frozenset[str]) -> Any:
        if not isinstance(current, dict):
            return current
        result = copy.deepcopy(current)
        if "$ref" in result:
            reference = result.pop("$ref")
            if reference in active:
                raise ValidationError("Materialized schemas require finite local references.")
            target = expand(references.resolve(reference), active | {reference})
            if target is False:
                return False
            metadata = {key: result.pop(key) for key in list(result) if key in annotations}
            result = schema_intersection([target, result]) if isinstance(target, dict) else result
            result.update(metadata)
        result.pop("$defs", None)
        for child in DRAFT202012.create_resource(result).subresources():
            if isinstance(child.contents, dict):
                expanded = expand(child.contents, active)
                child.contents.clear()
                child.contents.update(expanded if isinstance(expanded, dict) else {"not": {}})
        return result

    materialized = expand(schema, frozenset())
    return materialized if isinstance(materialized, dict) else {"not": {}}


def relation_positions(
    schema: dict[str, Any], value: Any,
) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any], str]]:
    """Locate string values at declared relation positions in a finite schema.

    Local references and allOf contribute together; anyOf/oneOf contribute only
    branches valid for the original value. Literal strings outside those
    positions carry no relation intent.
    """
    if not any(node.get("relation") for node in schema_nodes(schema)):
        return
    schema = materialize_schema(schema, annotations=SCHEMA_ANNOTATIONS | {"relation"})

    def visit(
        node: Any, item: Any, path: tuple[str | int, ...],
    ) -> Iterator[tuple[tuple[str | int, ...], dict[str, Any], str]]:
        if not isinstance(node, dict) or item is None:
            return
        if (relation := node.get("relation")) and isinstance(item, str):
            yield path, relation, item
        if isinstance(item, dict):
            for name, child in node.get("properties", {}).items():
                if name in item:
                    yield from visit(child, item[name], (*path, name))
        elif isinstance(item, list):
            prefix = node.get("prefixItems", ())
            for index, element in enumerate(item):
                yield from visit(prefix[index] if index < len(prefix) else node.get("items"), element, (*path, index))
        for child in node.get("allOf", ()):
            yield from visit(child, item, path)
        for choice in ("anyOf", "oneOf"):
            for child in node.get(choice, ()):
                if validator(child).is_valid(item):
                    yield from visit(child, item, path)

    yield from visit(schema, value, ())
