"""Versioned public-webform schema and submission validation.

The stored shape is the serializable ``@angee/ui`` form-spec vocabulary: an
object schema with named ``properties`` and a root ``required`` list.  Public
ingress deliberately accepts only scalar fields; relation, object, array, and
``any`` descriptors are rejected so a form answer can never become a record
target or another executable instruction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator

SCHEMA_VERSION = 1
SCALAR_TYPES = frozenset({"string", "integer", "number", "boolean"})
SUBMISSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
FIELD_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_FIELDS = 100


def default_webform_schema() -> dict[str, Any]:
    """Return a migration-safe empty v1 form-spec object."""

    return {"type": "object", "properties": {}}


@dataclass(frozen=True)
class WebformField:
    """One validated scalar property in a public form spec."""

    name: str
    kind: str
    label: str
    required: bool
    read_only: bool
    email: bool
    enum: tuple[Any, ...] | None
    const: Any
    has_const: bool
    max_length: int | None

    def validate(self, value: Any, *, max_bytes: int) -> str | None:
        """Return one answer error, or ``None`` when the scalar is valid."""

        if self.kind == "string":
            if not isinstance(value, str):
                return "Enter text."
            if self.required and not value.strip():
                return "This field is required."
            if self.max_length is not None and len(value) > self.max_length:
                return f"Use at most {self.max_length} characters."
            if self.email and value:
                try:
                    EmailValidator()(value)
                except ValidationError:
                    return "Enter a valid email address."
        elif self.kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                return "Enter an integer."
        elif self.kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return "Enter a number."
            try:
                json.dumps(value, allow_nan=False)
            except (TypeError, ValueError):
                return "Enter a finite number."
        elif self.kind == "boolean" and not isinstance(value, bool):
            return "Choose true or false."

        if self.enum is not None and value not in self.enum:
            return "Choose one of the declared options."
        if self.has_const and value != self.const:
            return "Use the form's declared value."
        if _scalar_size(value) > max_bytes:
            return f"This field exceeds {max_bytes} bytes."
        return None


@dataclass(frozen=True)
class WebformSpec:
    """The Python validation mirror of a versioned public form spec."""

    version: int
    fields: tuple[WebformField, ...]

    @classmethod
    def deserialize(cls, value: Any, *, version: int) -> WebformSpec:
        """Validate the stored schema and return its scalar field owners."""

        if version != SCHEMA_VERSION:
            raise ValidationError({"form_schema_version": f"Unsupported webform schema version {version}."})
        if not isinstance(value, dict):
            raise ValidationError({"form_schema": "A form schema must be an object."})
        if value.get("type", "object") != "object":
            raise ValidationError({"form_schema": 'The form schema root type must be "object".'})
        properties = value.get("properties", {})
        if not isinstance(properties, dict):
            raise ValidationError({"form_schema": "Form schema properties must be an object."})
        if len(properties) > MAX_FIELDS:
            raise ValidationError({"form_schema": f"A public form may declare at most {MAX_FIELDS} fields."})
        required_value = value.get("required", [])
        if not isinstance(required_value, list) or any(not isinstance(item, str) for item in required_value):
            raise ValidationError({"form_schema": "Form schema required must be a list of field names."})
        if len(set(required_value)) != len(required_value):
            raise ValidationError({"form_schema": "Form schema required field names must be unique."})
        unknown_required = sorted(set(required_value).difference(properties))
        if unknown_required:
            raise ValidationError({"form_schema": f"Required fields are not declared: {', '.join(unknown_required)}."})

        fields = tuple(
            _deserialize_field(name, descriptor, required=name in required_value)
            for name, descriptor in properties.items()
        )
        email_fields = [field.name for field in fields if field.email]
        if len(email_fields) > 1:
            raise ValidationError({"form_schema": "A public form may declare at most one email field."})
        return cls(version=version, fields=fields)

    @property
    def email_field(self) -> WebformField | None:
        """Return the schema-declared email field, if any."""

        return next((field for field in self.fields if field.email), None)

    def validate_answers(self, value: Any, *, max_field_bytes: int) -> dict[str, Any]:
        """Reject undeclared/non-scalar answers and return a lossless clean mapping."""

        if not isinstance(value, dict):
            raise ValidationError({"answers": "Answers must be a JSON object."})
        accepted = {field.name: field for field in self.fields if not field.read_only}
        extras = sorted(set(value).difference(accepted))
        errors: dict[str, str] = {}
        if extras:
            errors["answers"] = f"Unknown answer field(s): {', '.join(extras)}."
        for field in accepted.values():
            if field.name not in value:
                if field.required:
                    errors[field.name] = "This field is required."
                continue
            error = field.validate(value[field.name], max_bytes=max_field_bytes)
            if error:
                errors[field.name] = error
        if errors:
            raise ValidationError(errors)
        return {field.name: value[field.name] for field in self.fields if field.name in value and not field.read_only}

    def render_markdown(self, answers: dict[str, Any]) -> str:
        """Render validated answers in declaration order for the Message body."""

        blocks: list[str] = []
        for field in self.fields:
            if field.name not in answers:
                continue
            label = " ".join(field.label.split()) or field.name
            blocks.append(f"## {label}\n\n{_render_scalar(answers[field.name])}")
        return "\n\n".join(blocks)


def validate_submission_id(value: Any) -> str:
    """Return the caller's stable opaque receipt id after syntax validation."""

    if not isinstance(value, str) or not SUBMISSION_ID_RE.fullmatch(value):
        raise ValidationError({"submission_id": "Use 1-128 letters, numbers, dots, underscores, colons, or hyphens."})
    return value


def _deserialize_field(name: Any, value: Any, *, required: bool) -> WebformField:
    if not isinstance(name, str) or not FIELD_NAME_RE.fullmatch(name):
        raise ValidationError({"form_schema": f"Invalid public form field name {name!r}; use a letter-led identifier."})
    if not isinstance(value, dict):
        raise ValidationError({"form_schema": f"Field {name!r} must be an object."})
    if "relation" in value:
        raise ValidationError({"form_schema": f"Field {name!r} cannot declare a relation."})
    kind = value.get("type")
    if kind is None and isinstance(value.get("enum"), list):
        kind = "string"
    if kind not in SCALAR_TYPES:
        raise ValidationError({"form_schema": f"Field {name!r} must declare string, integer, number, or boolean type."})
    widget = value.get("widget")
    if widget is not None and (not isinstance(widget, str) or not widget):
        raise ValidationError({"form_schema": f"Field {name!r} widget must be a non-empty string."})
    email = widget == "email"
    if email and kind != "string":
        raise ValidationError({"form_schema": f"Email field {name!r} must have string type."})
    label = value.get("label", name)
    if not isinstance(label, str):
        raise ValidationError({"form_schema": f"Field {name!r} label must be a string."})
    for presentation_key in ("description", "placeholder"):
        presentation = value.get(presentation_key)
        if presentation is not None and not isinstance(presentation, str):
            raise ValidationError({"form_schema": f"Field {name!r} {presentation_key} must be a string."})
    read_only = value.get("readOnly", False)
    if not isinstance(read_only, bool):
        raise ValidationError({"form_schema": f"Field {name!r} readOnly must be a boolean."})
    if required and read_only:
        raise ValidationError({"form_schema": f"Required field {name!r} cannot be read-only."})
    max_length = value.get("maxLength")
    if max_length is not None and (
        kind != "string" or isinstance(max_length, bool) or not isinstance(max_length, int) or max_length < 1
    ):
        raise ValidationError(
            {"form_schema": f"Field {name!r} maxLength must be a positive integer on a string field."}
        )
    enum_value = value.get("enum")
    options_value = value.get("options")
    enum = None
    if enum_value is not None and options_value is not None:
        raise ValidationError({"form_schema": f"Field {name!r} cannot declare both enum and options."})
    if options_value is not None:
        if kind != "string" or not isinstance(options_value, list) or not options_value:
            raise ValidationError(
                {"form_schema": f"Field {name!r} options must be a non-empty list on a string field."}
            )
        option_values: list[str] = []
        for index, option in enumerate(options_value):
            if not isinstance(option, dict):
                raise ValidationError({"form_schema": f"Field {name!r} option {index} must be an object."})
            option_value = option.get("value")
            option_label = option.get("label")
            disabled = option.get("disabled", False)
            if not isinstance(option_value, str) or not option_value:
                raise ValidationError(
                    {"form_schema": f"Field {name!r} option {index} value must be a non-empty string."}
                )
            if not isinstance(option_label, str) or not option_label:
                raise ValidationError(
                    {"form_schema": f"Field {name!r} option {index} label must be a non-empty string."}
                )
            if not isinstance(disabled, bool):
                raise ValidationError({"form_schema": f"Field {name!r} option {index} disabled must be a boolean."})
            if option_value in option_values:
                raise ValidationError({"form_schema": f"Field {name!r} option values must be unique."})
            option_values.append(option_value)
        enum = tuple(option["value"] for option in options_value if not option.get("disabled", False))
        if not enum:
            raise ValidationError({"form_schema": f"Field {name!r} must have an enabled option."})
    elif enum_value is not None:
        if not isinstance(enum_value, list) or not enum_value:
            raise ValidationError({"form_schema": f"Field {name!r} enum must be a non-empty list."})
        if kind != "string" or any(not isinstance(item, str) for item in enum_value):
            raise ValidationError({"form_schema": f"Field {name!r} enum must contain only strings."})
        if len(set(enum_value)) != len(enum_value):
            raise ValidationError({"form_schema": f"Field {name!r} enum values must be unique."})
        enum = tuple(enum_value)
        for item in enum:
            error = WebformField(
                name=name,
                kind=kind,
                label=label,
                required=False,
                read_only=False,
                email=False,
                enum=None,
                const=None,
                has_const=False,
                max_length=max_length,
            ).validate(item, max_bytes=1_048_576)
            if error:
                raise ValidationError({"form_schema": f"Field {name!r} enum contains an invalid {kind} value."})
    field = WebformField(
        name=name,
        kind=kind,
        label=label,
        required=required,
        read_only=read_only,
        email=email,
        enum=enum,
        const=value.get("const"),
        has_const="const" in value,
        max_length=max_length,
    )
    for default_key in ("defaultValue", "const"):
        if default_key in value:
            error = field.validate(value[default_key], max_bytes=1_048_576)
            if error:
                raise ValidationError({"form_schema": f"Field {name!r} {default_key} is invalid: {error}"})
    return field


def _scalar_size(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8"))


def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, allow_nan=False)
