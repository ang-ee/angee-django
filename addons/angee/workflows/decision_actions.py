"""One checked Decision action schema and frozen read-only context contract."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Literal

from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ConfigDict, JsonValue, StrictBool, StrictInt, StrictStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError


class ReviewRecordReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: StrictStr
    id: StrictStr
    label: StrictStr = ""
    tab: StrictStr | None = None
    page: StrictInt | None = None
    search: dict[StrictStr, StrictStr | None] = {}


class ReviewFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    pointer: StrictStr
    label: StrictStr
    value: JsonValue
    subject: ReviewRecordReference | None = None
    authority: Literal["source", "correction", "unverified"]
    evidence: tuple[ReviewRecordReference, ...] = ()


class ReviewDifference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    field: StrictStr
    label: StrictStr
    left: JsonValue
    right: JsonValue
    changed: StrictBool
    leftRecord: ReviewRecordReference | None = None
    rightRecord: ReviewRecordReference | None = None


class ReviewReason(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    code: StrictStr
    parameters: dict[StrictStr, StrictStr | StrictInt | StrictBool] = {}


_CONTEXT_ADAPTERS = {
    "record": (TypeAdapter(ReviewRecordReference), TypeAdapter(tuple[ReviewRecordReference, ...])),
    "facts": (TypeAdapter(tuple[ReviewFact, ...]),),
    "differences": (TypeAdapter(tuple[ReviewDifference, ...]),),
    "reasons": (TypeAdapter(tuple[ReviewReason, ...]),),
    "object": (TypeAdapter(dict[StrictStr, JsonValue]),),
}
_VERDICTS = {"COMPLETE": "completed", "REJECT": "rejected", "ESCALATE": "escalated"}
_VARIANTS = {"primary", "secondary", "destructive", "ghost"}
_FORM_SPEC_OBJECT_EDGES = ("items", "if", "then", "else", "not", "additionalProperties")
_FORM_SPEC_ARRAY_EDGES = ("oneOf", "anyOf", "allOf")


def retained_decision_form_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Freeze authored field order before the displayed schema enters JSONB."""

    retained = copy.deepcopy(schema)

    def annotate(node: Any, *, path: str) -> None:
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            names = list(properties)
            order = node.get("propertyOrder")
            if order is None:
                node["propertyOrder"] = names
            elif (
                not isinstance(order, list)
                or any(not isinstance(name, str) or not name for name in order)
                or len(order) != len(set(order))
                or set(order) != set(names)
            ):
                raise ValidationError({
                    "decision_schema": (
                        f"{path}.propertyOrder must name every property exactly once."
                    )
                })
            for name, field in properties.items():
                annotate(field, path=f"{path}.properties.{name}")
        elif "propertyOrder" in node:
            raise ValidationError({
                "decision_schema": f"{path}.propertyOrder requires object properties."
            })

        definitions = node.get("$defs")
        if isinstance(definitions, dict):
            for name, field in definitions.items():
                annotate(field, path=f"{path}.$defs.{name}")
        for edge in _FORM_SPEC_OBJECT_EDGES:
            annotate(node.get(edge), path=f"{path}.{edge}")
        for edge in _FORM_SPEC_ARRAY_EDGES:
            choices = node.get(edge)
            if isinstance(choices, list):
                for index, choice in enumerate(choices):
                    annotate(choice, path=f"{path}.{edge}.{index}")

    annotate(retained, path="decision_schema")
    return retained


@dataclass(frozen=True, slots=True)
class DecisionActionContract:
    """Checked action branches and frozen presentation metadata of one schema."""

    schema: dict[str, Any]
    verdicts: dict[str, str]
    branches: dict[str, dict[str, Any]]
    context_fields: dict[str, str]

    def verdict_for(self, action: Any) -> str:
        if not isinstance(action, str) or action not in self.verdicts:
            raise ValidationError({"action": "Choose one declared Decision action."})
        return self.verdicts[action]

    def validate_context(self, payload: dict[str, Any]) -> None:
        """Check each frozen read-only value without treating it as input authority."""

        for name, widget in self.context_fields.items():
            if name not in payload:
                raise ValidationError({"payload": f"Decision context {name!r} is missing."})
            value = payload[name]
            field_schema = self.schema["properties"][name]
            # Keep root-local $defs visible when checking this one read-only
            # field; validating it in isolation would make a published $ref
            # fail only when an actor resolves the Decision.
            scoped_schema = {
                "$defs": self.schema.get("$defs", {}), "allOf": [field_schema],
            }
            errors = list(Draft202012Validator(
                scoped_schema, format_checker=FormatChecker(),
            ).iter_errors(value))
            if errors:
                raise ValidationError({"payload": f"Decision context {name!r} does not satisfy its schema."})
            if not any(_valid_context(adapter, value) for adapter in _CONTEXT_ADAPTERS[widget]):
                raise ValidationError({"payload": f"Decision context {name!r} is not a typed {widget} value."})


def _valid_context(adapter: TypeAdapter[Any], value: Any) -> bool:
    try:
        adapter.validate_json(json.dumps(value, allow_nan=False))
    except (PydanticValidationError, TypeError, ValueError):
        return False
    return True


def compile_decision_action_schema(schema: Any) -> DecisionActionContract | None:
    """Check the standard Draft 2020-12 tagged-action authoring profile once."""

    if schema in ({}, None):
        return None
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValidationError({"decision_schema": "Decision action schema root must be an object."})
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:  # jsonschema.SchemaError belongs at this public boundary.
        raise ValidationError({"decision_schema": f"Decision schema is invalid: {error}"}) from error
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not isinstance(properties.get("action"), dict):
        raise ValidationError({"decision_schema": "Decision schema must declare action properties."})
    action = properties["action"]
    values = action.get("enum")
    options = action.get("options")
    if (
        action.get("type") != "string"
        or not isinstance(values, list)
        or not values
        or any(not isinstance(value, str) or not value for value in values)
        or len(set(values)) != len(values)
        or not isinstance(options, list)
        or len(options) != len(values)
        or "action" not in schema.get("required", ())
    ):
        raise ValidationError({"decision_schema": "Action needs unique string enum/options and root required."})
    verdicts: dict[str, str] = {}
    for option in options:
        if not isinstance(option, dict) or set(option) - {"value", "label", "verdict", "variant", "confirm"}:
            raise ValidationError({"decision_schema": "Action option has unsupported metadata."})
        value = option.get("value")
        label = option.get("label")
        verdict = option.get("verdict")
        if (
            value not in values or value in verdicts
            or not isinstance(label, str) or not label
            or verdict not in _VERDICTS
            or ("variant" in option and option["variant"] not in _VARIANTS)
            or ("confirm" in option and not isinstance(option["confirm"], str))
        ):
            raise ValidationError({"decision_schema": "Action options must match enum and map native verdicts."})
        verdicts[value] = _VERDICTS[verdict]
    branches = schema.get("oneOf")
    if not isinstance(branches, list) or len(branches) != len(values):
        raise ValidationError({"decision_schema": "Declare one oneOf branch for every action."})
    context_fields: dict[str, str] = {}
    for name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            raise ValidationError({"decision_schema": f"Property {name!r} must be a schema object."})
        if field_schema.get("layout") == "context":
            widget = field_schema.get("widget")
            if widget not in _CONTEXT_ADAPTERS or name == "action":
                raise ValidationError({"decision_schema": f"Context property {name!r} needs a typed widget."})
            context_fields[str(name)] = widget
    if set(context_fields).intersection(schema.get("required", ())):
        raise ValidationError({
            "decision_schema": "Read-only Decision context cannot be required in submitted resolution."
        })
    checked: dict[str, dict[str, Any]] = {}
    for branch in branches:
        if (
            not isinstance(branch, dict)
            or branch.get("type") != "object"
            or branch.get("additionalProperties") is not False
            or not isinstance(branch.get("properties"), dict)
            or not isinstance(branch.get("required"), list)
            or "action" not in branch["required"]
        ):
            raise ValidationError({"decision_schema": "Each action branch must be a closed required-action object."})
        const = branch["properties"].get("action")
        value = const.get("const") if isinstance(const, dict) else None
        if value not in values or value in checked:
            raise ValidationError({"decision_schema": "Action branches need distinct enum const values."})
        admitted = set(branch["properties"])
        if admitted - set(properties) or admitted & set(context_fields) or set(branch["required"]) - admitted:
            raise ValidationError({"decision_schema": "Action branch admits undeclared/context or unlisted required fields."})
        checked[value] = branch
    if set(checked) != set(values) or set(verdicts) != set(values):
        raise ValidationError({"decision_schema": "Action options and branches must cover enum exactly."})
    return DecisionActionContract(schema, verdicts, checked, context_fields)
