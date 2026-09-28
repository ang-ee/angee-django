"""Pydantic action declarations compiled into durable, tagged decision forms."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, GetJsonSchemaHandler
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema

from angee.base.identity import relation_permission_validator
from angee.base.impl import (
    FORM_SCHEMA_ANNOTATIONS,
    check_form_annotations,
    freeze_form_schema,
    materialize_form_schema,
)
from angee.base.jsonschema import schema_nodes, validation_issues
from angee.decisions.states import Verdict


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


def relation_candidates(schema: dict[str, Any]) -> tuple[RelationCandidate, ...]:
    """Group frozen picker candidates by model and required permission for admission.

    Relation filters narrow picker queries; they are advisory on submission.
    Frozen option enums and the relation permission are enforced on submission.
    """
    grouped: dict[tuple[str, str], set[str]] = {}
    for field in schema_nodes(schema):
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
            branch = materialize_form_schema(original)
        except (KeyError, ValueError, TypeError, ValidationError) as error:
            raise ImproperlyConfigured(f"Invalid schema declaration on action {action.value}.") from error
        for node in schema_nodes(branch):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
            if "default" in node and validation_issues(node, node["default"]):
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
            if not isinstance(metadata, Mapping) or metadata.keys() - FORM_SCHEMA_ANNOTATIONS:
                raise ValidationError("Decision form refinements only accept presentation annotations.")
            fields[name].update(deepcopy(metadata))
            check_form_annotations(fields[name])
        if action.value in initial:
            freeze_form_schema(branch, dict(values))
        else:
            freeze_form_schema(branch)
        for name, field in fields.items():
            if "default" in field and validation_issues(field, field["default"]):
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
    if issues := validation_issues(
        branch, resolution, defaults=True,
        keywords={"relation": relation_permission_validator(actor)},
    ):
        raise ValidationError(issues)
    return Verdict(selected["verdict"]), resolution
