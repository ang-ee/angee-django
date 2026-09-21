"""One checked Decision action schema and frozen read-only context contract."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from django.apps import apps
from django.core.exceptions import ValidationError
from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import ValidationError as SchemaValidationError
from pydantic import BaseModel, ConfigDict, JsonValue, StrictInt, StrictStr, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from rebac.resources import model_resource_type
from referencing import Registry
from referencing.exceptions import Unresolvable
from referencing.jsonschema import DRAFT202012

from angee.base.identity import instance_from_public_id
from angee.base.scoping import read_scoped_queryset


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


class ReviewAction(BaseModel):
    """One authored action and the editable fields admitted by its branch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    value: StrictStr
    label: StrictStr
    verdict: Literal["COMPLETE", "REJECT", "ESCALATE"]
    fields: tuple[StrictStr, ...] = ()
    required: tuple[StrictStr, ...] = ()
    variant: Literal["primary", "secondary", "destructive", "ghost"] | None = None
    confirm: StrictStr | None = None

    def model_post_init(self, __context: Any) -> None:
        """Reject ambiguous or incomplete action declarations at authoring time."""

        del __context
        if not self.value or not self.label:
            raise ValueError("Decision actions require non-empty values and labels.")
        if len(set(self.fields)) != len(self.fields) or len(set(self.required)) != len(self.required):
            raise ValueError("Decision action fields and required fields must be unique.")
        if set(self.required) - set(self.fields):
            raise ValueError("Decision action required fields must be admitted by that action.")


@dataclass(frozen=True, slots=True)
class DecisionActionAuthoring:
    """One authored tagged-action schema and its typed read-only context payload."""

    decision_schema: dict[str, Any]
    payload: dict[str, Any]


def build_decision_action(
    *,
    actions: Collection[ReviewAction],
    properties: Mapping[str, Mapping[str, Any]] | None = None,
    payload: Mapping[str, Any] | None = None,
    facts: Collection[ReviewFact] = (),
    references: ReviewRecordReference | Collection[ReviewRecordReference] | None = None,
) -> DecisionActionAuthoring:
    """Build the one tagged Decision schema and typed review context contract.

    Consumers declare action metadata and ordinary editable property schemas;
    this owner emits the closed ``oneOf`` branches and serializes the standard
    review context models. The runtime validator remains the sole owner that
    admits the resulting schema when a Decision is retained.
    """

    declared = tuple(actions)
    if not declared or len({action.value for action in declared}) != len(declared):
        raise ValueError("Decision actions must contain distinct non-empty values.")
    editable = {str(name): copy.deepcopy(dict(schema)) for name, schema in (properties or {}).items()}
    if "action" in editable or any(not name for name in editable):
        raise ValueError("Decision editable properties require non-empty names other than 'action'.")
    for action in declared:
        unknown = set(action.fields) - set(editable)
        if unknown:
            raise ValueError(
                f"Decision action {action.value!r} admits undeclared fields: {', '.join(sorted(unknown))}."
            )

    context_values: dict[str, Any] = {}
    context_schemas: dict[str, dict[str, Any]] = {}
    definitions: dict[str, Any] = {}
    if facts:
        context_values["facts"] = _review_json(tuple(facts))
        context_schemas["facts"] = _context_schema(TypeAdapter(tuple[ReviewFact, ...]), "facts", definitions)
    if references is not None:
        if isinstance(references, ReviewRecordReference):
            context_values["references"] = _review_json(references)
            reference_adapter = TypeAdapter(ReviewRecordReference)
        else:
            retained_references = tuple(references)
            context_values["references"] = _review_json(retained_references)
            reference_adapter = TypeAdapter(tuple[ReviewRecordReference, ...])
        context_schemas["references"] = _context_schema(reference_adapter, "record", definitions)
    collisions = set(payload or {}).intersection(context_values)
    if collisions:
        raise ValueError(f"Decision payload cannot replace typed review context: {', '.join(sorted(collisions))}.")

    action_options = []
    branches = []
    for action in declared:
        option: dict[str, Any] = {
            "value": action.value,
            "label": action.label,
            "verdict": action.verdict,
        }
        if action.variant is not None:
            option["variant"] = action.variant
        if action.confirm is not None:
            option["confirm"] = action.confirm
        action_options.append(option)
        branches.append(
            {
                "type": "object",
                "required": ["action", *action.required],
                "properties": {
                    "action": {"const": action.value},
                    **{name: copy.deepcopy(editable[name]) for name in action.fields},
                },
                "additionalProperties": False,
            }
        )
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": [action.value for action in declared],
                "options": action_options,
            },
            **editable,
            **context_schemas,
        },
        "oneOf": branches,
    }
    if definitions:
        schema["$defs"] = definitions
    return DecisionActionAuthoring(
        decision_schema=schema,
        payload={**copy.deepcopy(dict(payload or {})), **context_values},
    )


def _context_schema(adapter: TypeAdapter[Any], widget: str, definitions: dict[str, Any]) -> dict[str, Any]:
    """Embed one Pydantic context schema while hoisting its root-local definitions."""

    schema = adapter.json_schema(mode="serialization")
    for name, definition in schema.pop("$defs", {}).items():
        existing = definitions.setdefault(name, definition)
        if existing != definition:
            raise ValueError(f"Review context definition {name!r} has competing schemas.")
    return {**schema, "layout": "context", "widget": widget}


def _review_json(value: Any) -> Any:
    """Serialize strict review models without accepting arbitrary coercion."""

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_review_json(item) for item in value]
    raise TypeError("Review context values must use their declared Pydantic models.")


_CONTEXT_ADAPTERS = {
    "record": (TypeAdapter(ReviewRecordReference), TypeAdapter(tuple[ReviewRecordReference, ...])),
    "facts": (TypeAdapter(tuple[ReviewFact, ...]),),
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
                raise ValidationError(
                    {"decision_schema": (f"{path}.propertyOrder must name every property exactly once.")}
                )
            for name, field in properties.items():
                annotate(field, path=f"{path}.properties.{name}")
        elif "propertyOrder" in node:
            raise ValidationError({"decision_schema": f"{path}.propertyOrder requires object properties."})

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
                "$defs": self.schema.get("$defs", {}),
                "allOf": [field_schema],
            }
            errors = list(
                Draft202012Validator(
                    scoped_schema,
                    format_checker=FormatChecker(),
                ).iter_errors(value)
            )
            if errors:
                raise ValidationError({"payload": f"Decision context {name!r} does not satisfy its schema."})
            if not any(_valid_context(adapter, value) for adapter in _CONTEXT_ADAPTERS[widget]):
                raise ValidationError({"payload": f"Decision context {name!r} is not a typed {widget} value."})


def _valid_context(adapter: TypeAdapter[Any], value: Any) -> bool:
    try:
        adapter.validate_json(json.dumps(value, allow_nan=False))
    except PydanticValidationError, TypeError, ValueError:
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
            not isinstance(value, str)
            or value not in values
            or value in verdicts
            or not isinstance(label, str)
            or not label
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
        raise ValidationError(
            {"decision_schema": "Read-only Decision context cannot be required in submitted resolution."}
        )
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
        if not isinstance(value, str) or value not in values or value in checked:
            raise ValidationError({"decision_schema": "Action branches need distinct enum const values."})
        admitted = set(branch["properties"])
        if admitted - set(properties) or admitted & set(context_fields) or set(branch["required"]) - admitted:
            raise ValidationError(
                {"decision_schema": "Action branch admits undeclared/context or unlisted required fields."}
            )
        checked[value] = branch
    if set(checked) != set(values) or set(verdicts) != set(values):
        raise ValidationError({"decision_schema": "Action options and branches must cover enum exactly."})
    return DecisionActionContract(schema, verdicts, checked, context_fields)


def validate_decision_resolution(
    decision: Any, payload: Any, *, actor: Any, verdict: str, using: str
) -> dict[str, Any]:
    """Validate values through JSON Schema or the native Python contract.

    JSON-authored Decisions retain self-contained Draft 2020-12 schemas: local
    references are resolved natively; remote references and nested dialect
    changes are rejected. Values are neither coerced nor defaulted. Python
    contracts retain Pydantic's native validation and serialization.
    """

    resolution = {} if payload is None else payload
    if not isinstance(resolution, dict):
        raise ValidationError({"payload": "Decision payload must be a JSON object."})
    schema = decision.form_schema
    if schema is None and decision.step_run.step_id is not None:
        schema = decision.step_run.step.resolve_impl("step_class").decision_schema
    if schema is None:
        return dict(resolution)
    if not isinstance(schema, dict):
        try:
            submitted = schema.model_validate(resolution).model_dump(mode="json")
        except PydanticValidationError as error:
            raise _resolution_validation_error(error) from error
        _validate_relation_fields(schema.model_json_schema(by_alias=False), submitted, actor, using=using)
        return submitted

    schema = _decision_validation_schema(schema)
    contract = compile_decision_action_schema(schema)
    properties = schema.get("properties", {})
    context_fields = {
        name for name, field in properties.items() if isinstance(field, dict) and field.get("layout") == "context"
    }
    submitted_context = context_fields.intersection(resolution)
    if submitted_context:
        raise ValidationError(
            {name: "Decision context cannot be submitted as a resolution." for name in sorted(submitted_context)}
        )
    resolution_schema = dict(schema)
    resolution_schema["properties"] = {name: field for name, field in properties.items() if name not in context_fields}
    if "required" in schema:
        resolution_schema["required"] = [name for name in schema["required"] if name not in context_fields]
    if contract is not None:
        selected = resolution.get("action")
        branch = contract.branches.get(selected) if isinstance(selected, str) else None
        if branch is None:
            raise ValidationError({"action": "Choose one declared Decision action."})
        extraneous = set(resolution) - set(branch["properties"])
        if extraneous:
            raise ValidationError(
                {name: "This field is not permitted for the selected action." for name in sorted(extraneous)}
            )
        if contract.verdict_for(selected) != str(verdict):
            raise ValidationError({"verdict": "The selected action maps to a different native verdict."})
    errors: dict[str, list[str]] = {}
    try:
        failures = sorted(
            Draft202012Validator(
                resolution_schema,
                format_checker=FormatChecker(),
                registry=Registry(),
            ).iter_errors(resolution),
            key=lambda item: (tuple(str(part) for part in item.path), item.message),
        )
    except Unresolvable as error:
        raise ValidationError(
            {"decision_schema": "Decision references must resolve inside the retained schema."}
        ) from error
    for failure in failures:
        field = ".".join(str(part) for part in failure.path) or "payload"
        errors.setdefault(field, []).append(failure.message)
    if errors:
        raise ValidationError(errors)
    _validate_relation_fields(resolution_schema, resolution, actor, using=using)
    return copy.deepcopy(resolution)


def _decision_validation_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep native validator evolution in the retained Draft 2020-12 dialect.

    ``Resource.subresources`` owns discovery of schema nodes, so instance data
    in defaults/examples and unknown annotations are never interpreted as schemas.
    Redundant dialect declarations are removed only from this validation copy;
    jsonschema would otherwise evolve back to its stock, annotation-free class.
    """

    retained = copy.deepcopy(schema)
    pending = [DRAFT202012.create_resource(retained)]
    dialect = Draft202012Validator.META_SCHEMA["$id"].rstrip("#")
    while pending:
        resource = pending.pop()
        contents = resource.contents
        if isinstance(contents, dict) and "$schema" in contents:
            declared = contents.pop("$schema")
            if not isinstance(declared, str) or declared.rstrip("#") != dialect:
                raise ValidationError({"decision_schema": "Decision schemas must use Draft 2020-12 throughout."})
        pending.extend(resource.subresources())
    return retained


def _validate_relation_fields(schema: dict[str, Any], resolution: dict[str, Any], actor: Any, *, using: str) -> None:
    """Authorize relation annotations through JSON Schema's native applicators.

    The native validator owns reference resolution, branch selection, nested
    objects and arrays. The extension adds only the declared record permission;
    actor scope is explicit and lasts for this validation call.
    """

    def relation_permission(
        validator: Any,
        relation: Any,
        value: Any,
        field_schema: Any,
    ) -> Iterator[SchemaValidationError]:
        del validator, field_schema
        message = (
            _relation_error(relation, value, actor, using=using)
            if isinstance(relation, dict)
            else "Relation metadata must be an object."
        )
        if message is not None:
            yield SchemaValidationError(message)

    def relation_errors(error: SchemaValidationError) -> Iterator[SchemaValidationError]:
        if error.validator == "relation":
            yield error
        for nested in error.context:
            yield from relation_errors(nested)

    relation_validator = validators.extend(Draft202012Validator, {"relation": relation_permission})
    errors: dict[str, list[str]] = {}
    try:
        failures = list(
            relation_validator(
                _decision_validation_schema(schema),
                format_checker=FormatChecker(),
                registry=Registry(),
            ).iter_errors(resolution)
        )
    except Unresolvable as error:
        raise ValidationError(
            {"decision_schema": "Decision references must resolve inside the retained schema."}
        ) from error
    for failure in failures:
        for relation_failure in relation_errors(failure):
            path = ".".join(str(part) for part in relation_failure.absolute_path) or "payload"
            messages = errors.setdefault(path, [])
            if relation_failure.message not in messages:
                messages.append(relation_failure.message)
    if errors:
        raise ValidationError(errors)


_RELATION_PERMISSION = re.compile(r"^[a-z][a-z0-9_]*$")


def _relation_error(relation: dict[str, Any], value: Any, actor: Any, *, using: str) -> str | None:
    """Return the field error for one submitted relation id, or None when valid.

    Unknown ids, wrong-model ids, and ids outside the declared permission scope
    share one message so the response does not disclose which records exist.
    """

    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return "Relation value must be a record id."
    resource = str(relation.get("resource") or "")
    app_label, separator, model_name = resource.partition(".")
    if not separator:
        return "Relation resource must be an app_label.Model string."
    try:
        model = apps.get_model(app_label, model_name)
    except LookupError, ValueError:
        return f"Relation resource {resource!r} is not installed."
    permission = relation.get("permission", "write")
    if not isinstance(permission, str) or _RELATION_PERMISSION.fullmatch(permission) is None:
        return "Relation value must reference a permitted record."
    scoped = read_scoped_queryset(model, actor, action=permission)
    if scoped is not None:
        scoped = scoped.using(using)
    if scoped is None and model_resource_type(model):
        return "Relation value must reference a permitted record."
    if scoped is None:
        scoped = model._default_manager.using(using)
    instance = instance_from_public_id(model, value, queryset=scoped)
    if instance is None:
        return (
            "Relation value must reference a record you can write."
            if permission == "write"
            else "Relation value must reference a permitted record."
        )
    return None


def _resolution_validation_error(error: PydanticValidationError) -> ValidationError:
    """Translate pydantic failures into field-keyed Django validation errors."""

    field_errors: dict[str, list[str]] = {}
    for detail in error.errors(include_url=False):
        field = ".".join(str(component) for component in detail["loc"]) or "payload"
        field_errors.setdefault(field, []).append(str(detail["msg"]))
    return ValidationError(field_errors)
