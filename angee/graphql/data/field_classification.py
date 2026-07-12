"""Field classification for Angee data-resource metadata."""

from __future__ import annotations

from typing import Any

from django.db import models

from angee.base.mixins import ARCHIVE_FLAG_FIELD
from angee.graphql.introspection import is_to_many_relation

RESOURCE_FIELD_KINDS = frozenset({"scalar", "enum", "relation", "list"})
"""Supported resource field kind names."""

RESOURCE_FIELD_SCALARS = frozenset({"ID", "String", "Boolean", "Int", "Float", "Decimal", "DateTime", "Date", "JSON"})
"""Supported GraphQL scalar families in data-resource field metadata."""

RESOURCE_FIELD_WIDGETS = frozenset(
    {"select", "many2one", "tagInput", "switch", "integer", "float", "money", "datetime", "date", "json"}
)
"""Widget vocabulary owned by backend data-resource metadata."""


def resource_field_kind(
    field: models.Field[Any, Any] | None,
    *,
    has_relation_axis: bool = False,
    is_list: bool = False,
    is_enum: bool = False,
    is_object: bool = False,
    projected_as_scalar: bool = False,
) -> str:
    """Return the coarse field kind used by data-resource metadata.

    A to-one relation classifies by how the node *projects* it: as a nested object
    (``is_object``) or group axis (``has_relation_axis``) it is a ``relation``; as a
    bare scalar id (``projected_as_scalar`` — an ``ID`` with no subfields) it is a
    scalar LEAF so the detail/form query selects it without an invalid
    sub-selection, while still carrying relation metadata (target label + scalar-id
    widget). Absent a known wire projection (model reconstruction with no surface),
    a relation stays an object ``relation``.
    """

    if is_list or (field is not None and is_to_many_relation(field)):
        return "list"
    if field is not None and field.is_relation and projected_as_scalar:
        return "scalar"
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
        # A to-one relation the node projects as a bare scalar id (kind == "scalar"):
        # the scalar-id relation widget selects and writes the flat id, never a
        # sub-object (a ``many2one`` selects ``<field>.id``, invalid on an ``ID``).
        return "select"
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
