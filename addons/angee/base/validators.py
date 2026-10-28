"""Translate typed declaration validation into Django's field error contract."""

import copy
from collections.abc import Callable, Mapping
from functools import cache
from typing import Any, get_args

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import RegexValidator
from jsonschema import Draft202012Validator, FormatChecker, SchemaError, validators
from jsonschema._utils import find_additional_properties
from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from referencing import Registry
from referencing.jsonschema import UnknownDialect

from angee.base.schemas.references import LocalSchemaReferences, schema_nodes
from angee.serialization import canonical_json

HEX_COLOR_VALIDATOR = RegexValidator(r"^#[0-9a-fA-F]{6}$", "Enter a colour as #rrggbb.")
"""The one colour form a colour column stores and the colour chooser writes."""


@cache
def get_type_adapter(type_: Any) -> TypeAdapter[Any]:
    """Reuse native parsing, schema and serialization for each declared type."""

    return TypeAdapter(Any if type_ is None else type_)


def validate_value(type_: Any, value: Any, *, field: str) -> Any:
    """Validate a typed value with error paths rooted at the owning field."""

    try:
        return get_type_adapter(type_).validate_python(value)
    except PydanticValidationError as error:
        messages: dict[str, list[str]] = {}
        for issue in error.errors(include_url=False, include_context=False, include_input=False):
            location = ".".join(str(part) for part in issue["loc"])
            path = f"{field}.{location}" if location else field
            messages.setdefault(path, []).append(str(issue["msg"]))
        raise ValidationError(messages) from None


def _pydantic_models_in(annotation: Any) -> tuple[type[BaseModel], ...]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return (annotation,)
    return tuple(model for argument in get_args(annotation) for model in _pydantic_models_in(argument))


def validate_config_aliases(
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
            validate_config_aliases(nested, owner=owner, path=field_path, seen=seen | {model})


def check_schema(schema: dict[str, Any] | bool) -> None:
    """Check a declaration and every local reference, including unused definitions.

    Root pointers and anchors are supported. Remote references, nested resource
    scopes and other schema dialects are rejected before validating any value.
    Native dynamic references are checked too; structural helpers remain more
    conservative than the validator.
    """
    try:
        Draft202012Validator.check_schema(schema)
        references = LocalSchemaReferences(schema)
        pending = list(schema_nodes(schema))
        visited: set[int] = set()
        for node in pending:
            if id(node) in visited:
                continue
            visited.add(id(node))
            if "$schema" in node and node["$schema"].rstrip("#") != Draft202012Validator.META_SCHEMA["$id"]:
                raise ValidationError("JSON Schema declarations require Draft 2020-12.")
            if node is not schema and "$id" in node:
                raise ValidationError("JSON Schema declarations do not support nested resource scopes.")
            for keyword in ("$ref", "$dynamicRef"):
                if keyword in node:
                    target = references.resolve(node[keyword])
                    if target is None:
                        raise ValidationError(f"Unresolved or non-local JSON Schema reference {node[keyword]!r}.")
                    Draft202012Validator.check_schema(target)
                    pending.extend(schema_nodes(target))
    except SchemaError as error:
        raise ValidationError(f"Invalid JSON Schema: {error.message}") from error
    except UnknownDialect as error:
        raise ValidationError(f"Unsupported JSON Schema dialect {error.uri!r}.") from error


def validator(schema: dict[str, Any] | bool) -> Draft202012Validator:
    """Construct a checked, format-asserting native validator without retrieval."""
    check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker(), registry=Registry())


def validate(schema: dict[str, Any] | bool | Draft202012Validator, value: Any) -> None:
    """Raise one Django validation error containing the native ordered messages."""
    checked = schema if isinstance(schema, Draft202012Validator) else validator(schema)
    errors = [f"{error.json_path}: {error.message}" for error in checked.iter_errors(value)]
    if errors:
        raise ValidationError(errors)


def validation_issues(
    schema: dict[str, Any], value: Any, *, defaults: bool = False,
    keywords: Mapping[str, Callable[..., Any]] | None = None,
) -> dict[str, list[str]]:
    """Return Django field errors through native keyword hooks and format checks.

    With defaults enabled, missing object properties receive copied defaults
    before assertions at every node, independent of serialized keyword order.
    The supplied value is mutated; callers retaining raw input must copy it.
    """
    try:
        canonical_json(value)
    except (ValueError, TypeError):
        return {"__all__": ["Values must contain finite JSON values."]}
    checked = validator(schema)

    def properties(native: Any, fields: dict[str, Any], instance: Any, parent: Any) -> Any:
        if defaults and isinstance(instance, dict):
            for name, field in fields.items():
                if name not in instance and isinstance(field, dict) and "default" in field:
                    instance[name] = copy.deepcopy(field["default"])
        yield from Draft202012Validator.VALIDATORS["properties"](native, fields, instance, parent)

    native_class = validators.create(
        meta_schema=checked.META_SCHEMA,
        validators={**checked.VALIDATORS, **(keywords or {}), "properties": properties},
        type_checker=checked.TYPE_CHECKER, format_checker=checked.FORMAT_CHECKER, id_of=checked.ID_OF,
        applicable_validators=lambda node: sorted(node.items(), key=lambda item: (item[0] != "properties", item[0])),
    )
    issues: dict[str, list[str]] = {}
    for error in native_class(schema, format_checker=checked.format_checker, registry=Registry()).iter_errors(value):
        path = list(error.absolute_path)
        fields = [path]
        if error.validator == "required":
            fields = [path + [name] for name in error.validator_value if name not in error.instance]
        elif error.validator == "additionalProperties" and isinstance(error.instance, dict):
            fields = [path + [name] for name in find_additional_properties(error.instance, error.schema)]
        for parts in fields:
            issues.setdefault(".".join(map(str, parts)) or "__all__", []).append(error.message)
    return issues
