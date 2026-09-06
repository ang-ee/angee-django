"""Django field classification and vocabulary for Angee data descriptions."""

from __future__ import annotations

import datetime
import decimal
from typing import Any

from django.db import models

from angee.base.mixins import ARCHIVE_FLAG_FIELD

RESOURCE_FIELD_KINDS = frozenset({"scalar", "enum", "relation", "list"})
"""Supported resource field kind names."""

RESOURCE_FIELD_SCALARS = frozenset({"ID", "String", "Boolean", "Int", "Float", "Decimal", "DateTime", "Date", "JSON"})
"""Supported GraphQL scalar families in data-resource field metadata."""

RESOURCE_FIELD_WIDGETS = frozenset(
    {"select", "many2one", "tagInput", "switch", "integer", "float", "money", "datetime", "date", "json"}
)
"""Widget vocabulary owned by backend data-resource metadata."""


def is_to_many_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether a Django field represents a to-many relation path."""

    return bool(getattr(field, "many_to_many", False) or getattr(field, "one_to_many", False))


def is_to_one_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether ``field`` is a forward to-one relation."""

    return bool(getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))


def resource_field_kind(
    field: models.Field[Any, Any] | None,
    *,
    has_relation_axis: bool = False,
    is_list: bool = False,
    is_enum: bool = False,
    is_object: bool = False,
) -> str:
    """Return the coarse field kind used by data-resource metadata.

    Django owns relation semantics even when GraphQL projects a to-one relation as
    a scalar ID. ``relation_object`` separately tells consumers whether the final
    executable field accepts a sub-selection.
    """

    if is_list or (field is not None and is_to_many_relation(field)):
        return "list"
    if is_object or has_relation_axis:
        return "relation"
    if field is not None and field.is_relation:
        return "relation"
    if is_enum or (field is not None and getattr(field, "choices", None)):
        return "enum"
    if field is not None and getattr(field, "many_to_many", False):
        return "list"
    return "scalar"


def model_field_scalar(field: models.Field[Any, Any]) -> str | None:
    """Return the GraphQL scalar a Django field's column type maps to, or None."""

    declared = _declared_projection_fact(field, "angee_scalar_hint")
    if declared is not None:
        return declared
    if isinstance(field, models.BooleanField):
        return "Boolean"
    if isinstance(field, models.IntegerField):
        return "Int"
    if isinstance(field, models.DecimalField):
        return "Decimal"
    if isinstance(field, models.FloatField):
        return "Float"
    if isinstance(field, models.DateTimeField):
        return "DateTime"
    if isinstance(field, models.DateField):
        return "Date"
    if isinstance(field, models.JSONField):
        return "JSON"
    if isinstance(field, (models.CharField, models.TextField, models.UUIDField)):
        return "String"
    return None


def python_type_scalar(python_type: object) -> str | None:
    """Return the metadata scalar for one native Python value type."""

    scalars: dict[object, str] = {
        str: "String",
        bool: "Boolean",
        int: "Int",
        float: "Float",
        decimal.Decimal: "Decimal",
        datetime.datetime: "DateTime",
        datetime.date: "Date",
    }
    return scalars.get(python_type)


def is_archive_field(field: models.Field[Any, Any] | None) -> bool:
    """Return whether ``field`` is the :class:`~angee.base.mixins.ArchiveMixin` flag.

    The archive vocabulary is name-based — one column name across the platform
    (:data:`angee.base.mixins.ARCHIVE_FLAG_FIELD`) — so any model composing
    ``ArchiveMixin`` is recognised by that column and marked ``archivable`` in
    resource metadata. A same-typed boolean under a different contract (a
    soft-delete ``is_trashed``, an enablement ``is_enabled``/``is_active``) is
    deliberately not matched.
    """

    return field is not None and getattr(field, "name", None) == ARCHIVE_FLAG_FIELD


def money_currency_field(field: models.Field[Any, Any] | None) -> str | None:
    """Return the currency path a field declares for money metadata, if any."""

    return _declared_projection_fact(field, "angee_currency_field")


def resource_field_widget(field: models.Field[Any, Any] | None, kind: str) -> str | None:
    """Return the default rendered widget owned by the field classification."""

    declared = _declared_projection_fact(field, "angee_widget")
    if declared is not None:
        return declared
    if kind == "enum":
        return "select"
    if kind == "relation":
        return "many2one"
    if kind == "list":
        if field is None or field.is_relation:
            return None
        return "tagInput"
    if field is None:
        return None
    if field.is_relation:
        return "many2one"
    if isinstance(field, models.BooleanField):
        return "switch"
    if isinstance(field, models.IntegerField):
        return "integer"
    if isinstance(field, (models.DecimalField, models.FloatField)):
        return "float"
    if isinstance(field, models.DateTimeField):
        return "datetime"
    if isinstance(field, models.DateField):
        return "date"
    if isinstance(field, models.JSONField):
        return "json"
    return None


def _declared_projection_fact(field: models.Field[Any, Any] | None, name: str) -> str | None:
    """Return one field-owned projection declaration, if present."""

    if field is None:
        return None
    value = getattr(field, name, None)
    if value in (None, ""):
        return None
    return str(value)
