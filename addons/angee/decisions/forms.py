"""Pydantic action declarations compiled into durable, tagged decision forms."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema
from referencing.jsonschema import DRAFT202012

from angee.base.identity import instances_from_public_ids
from angee.base.impl import _FORM_SPEC_RELATION_VALIDATOR
from angee.base.jsonschema import LocalSchemaReferences
from angee.base.scoping import read_scoped_queryset
from angee.decisions.states import Verdict

_ANNOTATIONS = frozenset({"title", "description", "widget", "relation", "options", "readOnly"})
# B10 stand-in: shared schema vocabulary and structural traversal belong to the base owner.
_SCHEMA_KEYS = frozenset(Draft202012Validator.VALIDATORS) | _ANNOTATIONS | {
    "$schema", "$defs", "$anchor", "default",
}
_SCHEMA_LISTS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_MISSING = object()


@dataclass(frozen=True)
class Relation:
    """Native Pydantic metadata for a standing-permission relation picker."""

    resource: str
    permission: str = "read"

    def __get_pydantic_json_schema__(self, schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        """Attach the base FormSpec relation contract to the native field schema."""
        return {**handler(schema), "relation": {"resource": self.resource, "permission": self.permission}}


@dataclass(frozen=True)
class RelationCandidate:
    """Frozen picker identities sharing one model and standing permission."""

    model: str
    permission: str
    ids: tuple[str, ...]


def _schemas(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """B10 stand-in: traverse only native Draft 2020-12 schema-bearing locations."""
    pending = [DRAFT202012.create_resource(schema)]
    while pending:
        resource = pending.pop()
        if isinstance(resource.contents, dict):
            yield resource.contents
        pending.extend(resource.subresources())


def relation_candidates(schema: dict[str, Any]) -> tuple[RelationCandidate, ...]:
    """Group frozen picker candidates by model and required permission for admission.

    Relation filters narrow picker queries; they are advisory on submission.
    Frozen option enums and the relation permission are enforced on submission.
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for field in _schemas(schema):
        if relation := field.get("relation"):
            ids = list(field.get("enum", ()))
            ids.extend(field[key] for key in ("default", "const") if key in field)
            grouped.setdefault((relation["resource"], relation.get("permission", "read")), set()).update(
                value for value in ids if isinstance(value, str)
            )
    return tuple(RelationCandidate(model, permission, tuple(sorted(ids)))
                 for (model, permission), ids in sorted(grouped.items()) if ids)


class Action(BaseModel):
    """One offered action, its terminal verdict, and its typed submitted fields."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    value: ClassVar[str]
    label: ClassVar[str]
    verdict: ClassVar[Verdict]

    def __init_subclass__(cls, *, value: str, label: str, verdict: Verdict, **kwargs: Any) -> None:
        """Bind action metadata, rejecting class declarations without a terminal verdict."""
        super().__init_subclass__(**kwargs)
        if not isinstance(value, str) or not value or not isinstance(label, str) or not label:
            raise ImproperlyConfigured("Actions require a value, label, and terminal verdict.")
        try:
            cls.value, cls.label, cls.verdict = value, label, Verdict(verdict)
        except (ValueError, TypeError) as error:
            raise ImproperlyConfigured("An action requires a known terminal verdict.") from error
        if cls.verdict == Verdict.PENDING:
            raise ImproperlyConfigured("An action requires a terminal verdict.")


def _materialize(schema: Any, references: LocalSchemaReferences, seen: tuple[str, ...] = ()) -> Any:
    """B10 stand-in: check declarations and resolve finite, root-local form references."""
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict) or set(schema) - _SCHEMA_KEYS:
        raise ImproperlyConfigured("Decision forms contain unsupported schema keywords or nested discriminators.")
    schema = deepcopy(schema)
    if reference := schema.pop("$ref", None):
        target = references.resolve(reference)
        if target is None or reference in seen:
            raise ImproperlyConfigured("Decision forms require finite, resolvable root-local references.")
        if set(schema) - _ANNOTATIONS - {"default"}:
            raise ImproperlyConfigured("Reference siblings must be form annotations.")
        schema = {**_materialize(target, references, (*seen, reference)), **schema}
    if "format" in schema and schema["format"] not in FormatChecker.checkers:
        raise ImproperlyConfigured(f"Unsupported decision form format: {schema['format']}.")
    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
    schema.pop("$defs", None)
    for child in DRAFT202012.create_resource(schema).subresources():
        if isinstance(child.contents, dict):
            normalized = _materialize(child.contents, references, seen)
            child.contents.clear()
            child.contents.update(normalized)
    return schema


def _metadata(schema: dict[str, Any], error_class: type[Exception]) -> None:
    """B10 stand-in: validate annotations through the existing FormSpec relation owner."""
    for name in ("title", "description", "widget"):
        if name in schema and not isinstance(schema[name], str):
            raise error_class(f"Form annotation {name} must be a string.")
    if "readOnly" in schema and not isinstance(schema["readOnly"], bool):
        raise error_class("Form annotation readOnly must be a boolean.")
    if "relation" in schema and (
        not _FORM_SPEC_RELATION_VALIDATOR.is_valid(schema["relation"])
        or schema.get("type") != "string"
    ):
        raise error_class("Relation fields require a string value and a valid FormSpec relation.")
    if "options" in schema:
        options = schema["options"]
        if not isinstance(options, list) or not options or any(
            not isinstance(option, dict) or set(option) != {"value", "label"}
            or not isinstance(option["label"], str) or _issues(schema, option["value"])
            for option in options
        ):
            raise error_class("Field options require valid values and labels.")


def _freeze(schema: Any, initial: Any = _MISSING) -> None:
    """B10 stand-in: bind stored defaults and immutable values to their compiled fields."""
    if not isinstance(schema, dict):
        return
    if "options" in schema:
        schema["enum"] = [deepcopy(option["value"]) for option in schema["options"]]
    if initial is not _MISSING:
        if "const" in schema and schema["const"] != initial:
            raise ValidationError("An initial value cannot change a declared constant.")
        schema["default"] = deepcopy(initial)
    value = schema.get("default", _MISSING)
    if schema.get("readOnly"):
        if value is _MISSING:
            raise ValidationError("A readOnly field requires an initial value or declared default.")
        schema["const"] = deepcopy(value)
    for name, child in schema.get("properties", {}).items():
        _freeze(child, value.get(name, _MISSING) if isinstance(value, dict) else _MISSING)
        if child.get("readOnly") and name not in schema.get("required", []):
            schema.setdefault("required", []).append(name)
    for key in _SCHEMA_LISTS:
        for child in schema.get(key, []):
            _freeze(child, value)
    if isinstance(value, list) and "items" in schema:
        # Per-row immutable values require per-row schemas, not one mutable shared item schema.
        schema["prefixItems"] = [deepcopy(schema["items"]) for _ in value]
        for child, item in zip(schema["prefixItems"], value, strict=True):
            _freeze(child, item)
    elif "items" in schema:
        _freeze(schema["items"])


def _issues(
    schema: dict[str, Any], values: Any, *, actor: Any = None, defaults: bool = False,
) -> dict[str, list[str]]:
    """B10 stand-in: assert formats and map native validation issues to Django fields."""
    issues: dict[str, list[str]] = {}
    try:
        json.dumps(values, allow_nan=False)
    except (ValueError, TypeError):
        return {"__all__": ["Form values must contain finite JSON values."]}
    def relation_permission(validator: Any, relation: dict[str, Any], value: Any, field: Any) -> Any:
        """B10 stand-in: revalidate relation permissions through native identity and scope owners."""
        if actor is None or not isinstance(value, str):
            return
        try:
            model = apps.get_model(relation["resource"])
        except (LookupError, ValueError):
            yield SchemaValidationError("The relation target is unavailable.")
            return
        queryset = read_scoped_queryset(model, actor, action=relation.get("permission", "read"))
        if queryset is None or value not in instances_from_public_ids(model, [value], queryset=queryset):
            yield SchemaValidationError("The selected record is absent or inaccessible.")

    def properties(validator: Any, fields: dict[str, Any], value: Any, parent: Any) -> Any:
        """B10 stand-in: apply frozen defaults through jsonschema's native value traversal."""
        if defaults and isinstance(value, dict):
            for name, field in fields.items():
                if name not in value and isinstance(field, dict) and "default" in field:
                    value[name] = deepcopy(field["default"])
        yield from Draft202012Validator.VALIDATORS["properties"](validator, fields, value, parent)

    # B10 stand-in: defaults precede assertions regardless of JSON storage key ordering.
    validator_class = validators.create(
        meta_schema=Draft202012Validator.META_SCHEMA,
        validators={**Draft202012Validator.VALIDATORS, "relation": relation_permission, "properties": properties},
        type_checker=Draft202012Validator.TYPE_CHECKER,
        format_checker=Draft202012Validator.FORMAT_CHECKER,
        id_of=Draft202012Validator.ID_OF,
        applicable_validators=lambda node: sorted(node.items(), key=lambda item: (item[0] != "properties", item[0])),
    )
    for error in validator_class(schema, format_checker=FormatChecker()).iter_errors(values):
        path = list(error.absolute_path)
        fields = [path]
        if error.validator == "required":
            fields = [path + [name] for name in error.validator_value if name not in error.instance]
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            fields = [path + [name] for name in error.instance.keys() - error.schema.get("properties", {}).keys()]
        for parts in fields:
            issues.setdefault(".".join(map(str, parts)) or "__all__", []).append(error.message)
    return issues


def compile_form(
    actions: Sequence[type[Action]], *,
    initial: Mapping[str, Mapping[str, Any]] | None = None,
    refine: Mapping[str, Mapping[str, Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Compile a snapshot; initial and refine are keyed by action, then declared field.

    Refinements contain only the closed presentation annotations. Initial values
    become JSON Schema defaults; readOnly values become required constants.
    """
    initial, refine = initial if initial is not None else {}, refine if refine is not None else {}
    if not all(isinstance(action, type) and issubclass(action, Action) and action is not Action for action in actions):
        raise ValidationError("Decision forms require declared action classes.")
    names = [action.value for action in actions]
    if not names or len(names) != len(set(names)):
        raise ValidationError("Decision forms require unique actions.")
    if not isinstance(initial, Mapping) or not isinstance(refine, Mapping) or (
        initial.keys() | refine.keys()
    ) - set(names):
        raise ValidationError("Initial and refine must map known action keys to field mappings.")
    branches, options = [], []
    for action in actions:
        try:
            original = action.model_json_schema(mode="validation")
            Draft202012Validator.check_schema(original)
        except (KeyError, ValueError, TypeError, SchemaError) as error:
            raise ImproperlyConfigured(f"Invalid schema declaration on action {action.value}.") from error
        branch = _materialize(original, LocalSchemaReferences(original))
        for node in _schemas(branch):
            _metadata(node, ImproperlyConfigured)
            if "default" in node and _issues(node, node["default"]):
                raise ImproperlyConfigured(f"Invalid declared default on action {action.value}.")
        fields = branch.get("properties", {})
        if "action" in fields or "action" in action.model_fields:
            raise ImproperlyConfigured("The action field is reserved by decision forms.")
        values, refinements = initial.get(action.value, {}), refine.get(action.value, {})
        if not isinstance(values, Mapping) or not isinstance(refinements, Mapping) or (
            values.keys() | refinements.keys()
        ) - fields.keys():
            raise ValidationError("Initial values and refinements must map declared action fields.")
        for name, metadata in refine.get(action.value, {}).items():
            if not isinstance(metadata, Mapping) or metadata.keys() - _ANNOTATIONS:
                raise ValidationError("Decision form refinements only accept presentation annotations.")
            fields[name].update(deepcopy(metadata))
            _metadata(fields[name], ValidationError)
        _freeze(branch, dict(initial[action.value]) if action.value in initial else _MISSING)
        for name, field in fields.items():
            if "default" in field and _issues(field, field["default"]):
                raise ValidationError(f"Invalid initial/default value for {action.value}.{name}.")
        fields["action"] = {"type": "string", "const": action.value}
        branch.update(properties=fields, additionalProperties=False)
        branch.setdefault("required", []).append("action")
        branches.append(branch)
        options.append({"value": action.value, "label": action.label, "verdict": str(action.verdict)})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
        "properties": {"action": {"type": "string", "enum": names, "options": options}},
        "required": ["action"], "discriminator": {"propertyName": "action"}, "oneOf": branches,
    }


def validate_form(
    schema: dict[str, Any], action: str, values: Any, *, actor: Any = None,
) -> tuple[Verdict, dict[str, Any]]:
    """Validate strictly against the admitted snapshot, returning its recorded resolution."""
    choices = schema["properties"]["action"]["options"]
    selected = next((choice for choice in choices if choice["value"] == action), None)
    if selected is None:
        raise ValidationError({"action": "This action is not offered by the decision."})
    if not isinstance(values, dict):
        raise ValidationError({"__all__": "Action values must be an object."})
    if "action" in values:
        raise ValidationError({"action": "Submit the action separately from its values."})
    resolution = {"action": action, **deepcopy(values)}
    branch = next(branch for branch in schema["oneOf"] if branch["properties"]["action"]["const"] == action)
    if issues := _issues(branch, resolution, actor=actor, defaults=True):
        raise ValidationError(issues)
    return Verdict(selected["verdict"]), resolution
