"""Field classification for Angee data-resource metadata."""

from __future__ import annotations

from typing import Any

from django.db import models

from angee.graphql.introspection import is_to_many_relation

RESOURCE_FIELD_KINDS = frozenset({"scalar", "enum", "relation", "list"})
"""Supported resource field kind names."""

RESOURCE_FIELD_SCALARS = frozenset({"ID", "String", "Boolean", "Int", "Float", "DateTime", "Date", "JSON"})
"""Supported GraphQL scalar families in data-resource field metadata."""

RESOURCE_FIELD_WIDGETS = frozenset(
    {"select", "many2one", "tagInput", "switch", "integer", "float", "datetime", "date", "json"}
)
"""Widget vocabulary owned by backend data-resource metadata."""


def resource_field_kind(
    field: models.Field[Any, Any] | None,
    *,
    has_relation_axis: bool = False,
    is_list: bool = False,
    is_enum: bool = False,
    is_object: bool = False,
) -> str:
    """Return the coarse field kind used by data-resource metadata."""

    if is_list or (field is not None and is_to_many_relation(field)):
        return "list"
    if is_object or has_relation_axis or (field is not None and field.is_relation):
        return "relation"
    if is_enum or (field is not None and getattr(field, "choices", None)):
        return "enum"
    if field is not None and getattr(field, "many_to_many", False):
        return "list"
    return "scalar"


def model_field_scalar(field: models.Field[Any, Any]) -> str | None:
    """Return the GraphQL scalar a Django field's column type maps to, or None."""

    if isinstance(field, models.BooleanField):
        return "Boolean"
    if isinstance(field, models.IntegerField):
        return "Int"
    if isinstance(field, (models.DecimalField, models.FloatField)):
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


def resource_field_widget(field: models.Field[Any, Any] | None, kind: str) -> str | None:
    """Return the default rendered widget owned by the field classification."""

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
