"""Lossless authoring projection for event-trigger Django lookup conditions."""

from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from typing import Annotated, Any, Literal

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from pydantic import BaseModel, Field, create_model

from angee.base.impl import model_config_form_spec
from angee.data.field_classification import model_field_scalar

ConditionScalar = Literal["boolean", "date", "datetime", "integer", "number", "string"]
_CONDITION_SCALARS: dict[str, ConditionScalar] = {
    "Boolean": "boolean",
    "Date": "date",
    "DateTime": "datetime",
    "Decimal": "number",
    "Float": "number",
    "Int": "integer",
    "String": "string",
    "UUID": "string",
}
_COMMON_LOOKUPS = frozenset({"exact", "in", "isnull"})
_ORDERED_LOOKUPS = frozenset({"gt", "gte", "lt", "lte", "range"})
_STRING_LOOKUPS = frozenset({
    "contains",
    "endswith",
    "icontains",
    "iendswith",
    "istartswith",
    "startswith",
})


@dataclass(frozen=True, slots=True)
class EventConditionField:
    """One readable concrete publisher field and its native lookup vocabulary."""

    name: str
    label: str
    scalar: ConditionScalar
    lookups: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventConditionLookup:
    """One authorable lookup with its exact persisted key and value schema."""

    name: str
    key: str
    label: str
    value_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EventConditionClause:
    """One supported conjunction clause, retaining its original persisted key."""

    field: str
    lookup: str
    value: Any
    source_key: str | None = None


@dataclass(frozen=True, slots=True)
class DecodedEventCondition:
    """Supported clauses plus untouched entries the catalogue cannot prove."""

    clauses: tuple[EventConditionClause, ...]
    opaque: dict[str, Any]
    errors: tuple[str, ...] = ()


class EventConditionCatalogue:
    """Django-owned lookup catalogue and lossless condition codec for one model."""

    def __init__(self, fields: Iterable[EventConditionField]) -> None:
        self.fields = tuple(fields)
        self._fields = {field.name: field for field in self.fields}

    @classmethod
    def from_model(
        cls,
        model: type[models.Model],
        *,
        readable_fields: Iterable[str],
    ) -> EventConditionCatalogue:
        """Catalogue readable concrete fields without widening schema authorization."""

        fields: list[EventConditionField] = []
        for name in sorted(set(readable_fields)):
            try:
                field = model._meta.get_field(name)
            except FieldDoesNotExist:
                continue
            scalar = _condition_scalar(field)
            if scalar is None or not field.concrete or field.many_to_many:
                continue
            fields.append(
                EventConditionField(
                    name=name,
                    label=str(field.verbose_name),
                    scalar=scalar,
                    lookups=tuple(sorted(set(field.get_lookups()) & _supported_lookups(scalar))),
                )
            )
        return cls(fields)

    def authored_lookups(self, field_name: str) -> tuple[EventConditionLookup, ...]:
        """Project native controls without asking clients to interpret lookup names."""

        field = self._fields[field_name]
        return tuple(
            EventConditionLookup(
                name=lookup,
                key=field.name if lookup == "exact" else f"{field.name}__{lookup}",
                label=_LOOKUP_LABELS[lookup],
                value_schema=_lookup_value_schema(field.scalar, lookup),
            )
            for lookup in field.lookups
        )

    def decode(self, condition: object) -> DecodedEventCondition:
        """Project supported clauses while retaining every unsupported entry exactly."""

        if not isinstance(condition, Mapping) or not all(isinstance(key, str) for key in condition):
            return DecodedEventCondition((), {}, ("Condition must be a JSON object.",))
        clauses: list[EventConditionClause] = []
        opaque: dict[str, Any] = {}
        for key, value in condition.items():
            parsed = self._parse_key(key)
            if parsed is None:
                opaque[key] = copy.deepcopy(value)
                continue
            field, lookup = parsed
            if not _valid_operand(self._fields[field].scalar, lookup, value):
                opaque[key] = copy.deepcopy(value)
                continue
            clauses.append(EventConditionClause(field, lookup, copy.deepcopy(value), key))

        counts = Counter((clause.field, clause.lookup) for clause in clauses)
        duplicates = {identity for identity, count in counts.items() if count > 1}
        if not duplicates:
            return DecodedEventCondition(tuple(clauses), opaque)
        supported: list[EventConditionClause] = []
        for clause in clauses:
            if (clause.field, clause.lookup) in duplicates:
                assert clause.source_key is not None
                opaque[clause.source_key] = copy.deepcopy(clause.value)
            else:
                supported.append(clause)
        errors = tuple(
            f"Condition contains more than one spelling for {field} {lookup}."
            for field, lookup in sorted(duplicates)
        )
        return DecodedEventCondition(tuple(supported), opaque, errors)

    def encode(
        self,
        clauses: Iterable[EventConditionClause],
        *,
        opaque: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge edited clauses with opaque entries, rejecting key or semantic collisions."""

        result = copy.deepcopy(dict(opaque))
        identities: dict[tuple[str, str], str] = {}
        for key in result:
            parsed = self._parse_key(key)
            if parsed is not None:
                identities[parsed] = key
        for clause in clauses:
            field = self._fields.get(clause.field)
            if field is None or clause.lookup not in field.lookups:
                raise ValidationError({"condition": f"Unsupported condition {clause.field} {clause.lookup}."})
            if not _valid_operand(field.scalar, clause.lookup, clause.value):
                raise ValidationError(
                    {"condition": f"Condition value for {clause.field} {clause.lookup} is invalid."}
                )
            identity = (clause.field, clause.lookup)
            key = (
                clause.source_key
                if clause.source_key is not None and self._parse_key(clause.source_key) == identity
                else clause.field if clause.lookup == "exact" else f"{clause.field}__{clause.lookup}"
            )
            if key in result or identity in identities:
                raise ValidationError({"condition": f"Condition lookup {key!r} is duplicated."})
            identities[identity] = key
            result[key] = copy.deepcopy(clause.value)
        return result

    def _parse_key(self, key: str) -> tuple[str, str] | None:
        for name in sorted(self._fields, key=lambda item: (-len(item), item)):
            if key == name:
                lookup = "exact"
            elif key.startswith(f"{name}__"):
                lookup = key[len(name) + 2 :]
            else:
                continue
            if lookup in self._fields[name].lookups:
                return name, lookup
        return None


def _condition_scalar(field: models.Field[Any, Any]) -> ConditionScalar | None:
    scalar = model_field_scalar(field)
    return None if scalar is None else _CONDITION_SCALARS.get(scalar)


def _supported_lookups(scalar: ConditionScalar) -> frozenset[str]:
    if scalar == "string":
        return _COMMON_LOOKUPS | _ORDERED_LOOKUPS | _STRING_LOOKUPS
    if scalar in {"date", "datetime", "integer", "number"}:
        return _COMMON_LOOKUPS | _ORDERED_LOOKUPS
    return _COMMON_LOOKUPS


def _valid_operand(scalar: ConditionScalar, lookup: str, value: Any) -> bool:
    try:
        payload = json.dumps({"value": value}, allow_nan=False)
        _lookup_value_model(scalar, lookup).model_validate_json(payload, strict=True)
    except (TypeError, ValueError):
        return False
    return True


_LOOKUP_LABELS = {
    "contains": "Contains",
    "endswith": "Ends with",
    "exact": "Is",
    "gt": "Is greater than",
    "gte": "Is at least",
    "icontains": "Contains (case insensitive)",
    "iendswith": "Ends with (case insensitive)",
    "in": "Is one of",
    "isnull": "Is empty",
    "istartswith": "Starts with (case insensitive)",
    "lt": "Is less than",
    "lte": "Is at most",
    "range": "Is in range",
    "startswith": "Starts with",
}


def _lookup_value_schema(scalar: ConditionScalar, lookup: str) -> dict[str, Any]:
    model = _lookup_value_model(scalar, lookup)
    return copy.deepcopy(model_config_form_spec(model, owner="EventConditionLookup")["properties"]["value"])


@cache
def _lookup_value_model(scalar: ConditionScalar, lookup: str) -> type[BaseModel]:
    scalar_type: Any = {
        "boolean": bool,
        "date": date,
        "datetime": datetime,
        "integer": int,
        "number": float,
        "string": str,
    }[scalar]
    annotation: Any
    if lookup == "isnull":
        annotation = bool
    elif lookup == "range":
        annotation = Annotated[list[scalar_type | None], Field(min_length=2, max_length=2)]
    elif lookup == "in":
        annotation = list[scalar_type | None]
    elif lookup == "exact":
        annotation = scalar_type | None
    else:
        annotation = scalar_type
    return create_model("EventConditionValue", value=(annotation, ...))


__all__ = [
    "ConditionScalar",
    "DecodedEventCondition",
    "EventConditionCatalogue",
    "EventConditionClause",
    "EventConditionField",
    "EventConditionLookup",
]
