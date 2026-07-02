"""Resource field metadata and Strawberry/Django field classification."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryList, StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry_django_hasura import SnakeNameConverter

from angee.base.fields import ImplClassField
from angee.graphql.data.field_classification import (
    RESOURCE_FIELD_KINDS as _RESOURCE_FIELD_KINDS,
)
from angee.graphql.data.field_classification import (
    RESOURCE_FIELD_SCALARS as _RESOURCE_FIELD_SCALARS,
)
from angee.graphql.data.field_classification import (
    RESOURCE_FIELD_WIDGETS as _RESOURCE_FIELD_WIDGETS,
)
from angee.graphql.data.field_classification import (
    model_field_scalar,
    resource_field_kind,
    resource_field_widget,
)
from angee.graphql.introspection import surface_field_names, surface_name

_FILTER_CONTROL_FIELDS = frozenset({"AND", "OR", "NOT", "DISTINCT", "and", "or", "not", "distinct"})


@dataclass(frozen=True, slots=True)
class DataRelationAxisMetadata:
    """Metadata for a relation group axis and its public identity lookup."""

    field: str
    model_label: str
    public_id_field: str
    label_axis: str | None = None


@dataclass(frozen=True, slots=True)
class DataResourceEnumValueMetadata:
    """One enum value exposed by a resource field."""

    value: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DataResourceFieldMetadata:
    """Field capability metadata emitted for one model resource field."""

    name: str
    kind: str
    scalar: str | None = None
    values: tuple[DataResourceEnumValueMetadata, ...] = ()
    widget: str | None = None
    readable: bool = True
    filterable: bool = False
    sortable: bool = False
    aggregatable: bool = False
    groupable: bool = False
    creatable: bool = False
    updatable: bool = False
    required_on_create: bool = False
    relation_model_label: str | None = None
    relation_label_axis: str | None = None


# The schema is built with ``hasura_config()`` (``angee/graphql/schema.py``); its
# ``SnakeNameConverter`` owns the python-name -> wire-name rule, keeping snake_case
# verbatim unless a field pins an explicit ``graphql_name``. The metadata the
# frontend codegen reads must name every field exactly as the schema does, so it
# asks the same converter instead of re-deriving the rule.
_WIRE_NAME_CONVERTER = SnakeNameConverter()


def resource_wire_field_name(surface: type | None, name: str | None) -> str | None:
    """Return the actual GraphQL wire field name for a Strawberry surface field."""

    if surface is None or name is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        for field in definition.fields:
            if field.python_name == name:
                return _wire_field_name(field)
    return name


def resource_wire_field_names(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return all declared GraphQL wire field names for a Strawberry surface."""

    if surface is None:
        return ()
    excluded = set(exclude)
    return tuple(
        resource_wire_field_name(surface, name) or name
        for name in surface_field_names(surface)
        if name not in excluded
    )


def resource_type_name(surface: type | None) -> str | None:
    """Return the GraphQL type name for ``surface`` when present."""

    if surface is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        return str(definition.name)
    definition = getattr(surface, "__strawberry_definition__", None)
    if definition is not None:
        return str(definition.name)
    return surface_name(surface)


def model_resource_fields(
    model: type[models.Model],
    fields: tuple[str, ...],
    *,
    filter_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    aggregate_fields: tuple[str, ...] = (),
    group_by_fields: tuple[str, ...] = (),
    create_fields: tuple[str, ...] = (),
    update_fields: tuple[str, ...] = (),
    required_create_fields: tuple[str, ...] = (),
    relation_axes: tuple[DataRelationAxisMetadata, ...] = (),
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return resource metadata for model fields exposed outside the node class."""

    filterable = set(filter_fields)
    sortable = set(order_fields)
    aggregatable = set(aggregate_fields)
    groupable = set(group_by_fields)
    creatable = set(create_fields)
    updatable = set(update_fields)
    required_on_create = set(required_create_fields)
    relation_by_field = {axis.field: axis for axis in relation_axes}
    return tuple(
        _model_resource_field(
            model,
            name,
            relation_axis=relation_by_field.get(name),
            filterable=name in filterable,
            sortable=name in sortable,
            aggregatable=name in aggregatable,
            groupable=name in groupable,
            creatable=name in creatable,
            updatable=name in updatable,
            required_on_create=name in required_on_create,
        )
        for name in fields
    )


def input_wire_fields(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return declared input fields as GraphQL wire names."""

    excluded = set(exclude)
    return tuple(
        resource_wire_field_name(surface, name) or name
        for name in _input_fields(surface)
        if name not in excluded
    )


def required_input_wire_fields(surface: type | None) -> tuple[str, ...]:
    """Return input fields whose value is required by GraphQL coercion."""

    if surface is None:
        return ()
    definition = get_object_definition(surface)
    if definition is None:
        return ()
    required: list[str] = []
    for field in definition.fields:
        if field.python_name in _FILTER_CONTROL_FIELDS:
            continue
        default = getattr(field, "default", dataclasses.MISSING)
        default_factory = getattr(field, "default_factory", dataclasses.MISSING)
        if default is not dataclasses.MISSING or default_factory is not dataclasses.MISSING:
            continue
        required.append(_wire_field_name(field))
    return tuple(required)


def resource_fields(
    node_type: type,
    model: type[models.Model] | None,
    *,
    filter_fields: tuple[str, ...],
    order_fields: tuple[str, ...],
    aggregate_fields: tuple[str, ...],
    group_by_fields: tuple[str, ...],
    create_fields: tuple[str, ...],
    update_fields: tuple[str, ...],
    required_create_fields: tuple[str, ...],
    relation_axes: tuple[DataRelationAxisMetadata, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return model resource field metadata from the declared node surface."""

    filterable = set(filter_fields)
    sortable = set(order_fields)
    aggregatable = set(aggregate_fields)
    groupable = set(group_by_fields)
    creatable = set(create_fields)
    updatable = set(update_fields)
    required_on_create = set(required_create_fields)
    relation_by_field = {axis.field: axis for axis in relation_axes}
    fields: list[DataResourceFieldMetadata] = []
    for python_name in surface_field_names(node_type):
        name = resource_wire_field_name(node_type, python_name) or python_name
        axis = relation_by_field.get(name)
        model_field = _model_field_or_none(model, python_name)
        surface_type = _surface_field_type(node_type, python_name)
        kind = resource_field_kind(
            model_field,
            has_relation_axis=axis is not None,
            is_list=_strawberry_type_is_list(surface_type),
            is_enum=_strawberry_type_is_enum(surface_type),
            is_object=_strawberry_type_is_object(surface_type),
        )
        scalar = _surface_field_scalar(
            surface=node_type,
            field_name=name,
            value=surface_type,
            kind=kind,
        )
        values = _resource_enum_values(model_field, surface_type) if kind == "enum" else ()
        fields.append(
            DataResourceFieldMetadata(
                name=name,
                kind=kind,
                scalar=scalar,
                values=values,
                widget=None if scalar == "ID" else resource_field_widget(model_field, kind),
                filterable=name in filterable,
                sortable=name in sortable,
                aggregatable=name in aggregatable,
                groupable=name in groupable,
                creatable=name in creatable,
                updatable=name in updatable,
                required_on_create=name in required_on_create,
                relation_model_label=_relation_model_label(model_field, axis),
                relation_label_axis=axis.label_axis if axis is not None else None,
            )
        )
    return tuple(fields)


def require_unique_resource_fields(
    model_label: str,
    fields: tuple[DataResourceFieldMetadata, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return resource field metadata after rejecting duplicate field names."""

    seen: set[str] = set()
    for field in fields:
        if field.name in seen:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares duplicate resource field '{field.name}'."
            )
        seen.add(field.name)
        _validate_resource_field(model_label, field)
    return fields


def merge_resource_fields(
    left: tuple[DataResourceFieldMetadata, ...],
    right: tuple[DataResourceFieldMetadata, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return resource field metadata merged by field name."""

    by_name = {field.name: field for field in left}
    order = [field.name for field in left]
    for field in right:
        existing = by_name.get(field.name)
        if existing is None:
            by_name[field.name] = field
            order.append(field.name)
            continue
        by_name[field.name] = DataResourceFieldMetadata(
            name=existing.name,
            kind=existing.kind if existing.kind != "scalar" or field.kind == "scalar" else field.kind,
            scalar=existing.scalar or field.scalar,
            values=existing.values or field.values,
            widget=existing.widget or field.widget,
            readable=existing.readable or field.readable,
            filterable=existing.filterable or field.filterable,
            sortable=existing.sortable or field.sortable,
            aggregatable=existing.aggregatable or field.aggregatable,
            groupable=existing.groupable or field.groupable,
            creatable=existing.creatable or field.creatable,
            updatable=existing.updatable or field.updatable,
            required_on_create=existing.required_on_create or field.required_on_create,
            relation_model_label=existing.relation_model_label or field.relation_model_label,
            relation_label_axis=existing.relation_label_axis or field.relation_label_axis,
        )
    return tuple(by_name[name] for name in order)


def _validate_resource_field(model_label: str, field: DataResourceFieldMetadata) -> None:
    """Reject impossible explicit resource field metadata."""

    if field.kind not in _RESOURCE_FIELD_KINDS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported kind '{field.kind}'."
        )
    if field.scalar is not None and field.scalar not in _RESOURCE_FIELD_SCALARS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported scalar '{field.scalar}'."
        )
    if field.widget is not None and field.widget not in _RESOURCE_FIELD_WIDGETS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares unsupported widget '{field.widget}'."
        )
    if field.kind in {"enum", "relation"} and field.scalar is not None:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"scalar '{field.scalar}' for {field.kind} fields."
        )
    if field.kind == "relation" and field.widget not in {None, "many2one"}:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"widget '{field.widget}' for relation fields."
        )
    if field.kind == "enum" and field.widget not in {None, "select"}:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' cannot declare "
            f"widget '{field.widget}' for enum fields."
        )


def _input_fields(surface: type | None) -> tuple[str, ...]:
    """Return declared input fields, excluding Strawberry-Django filter controls."""

    if surface is None:
        return ()
    return tuple(name for name in surface_field_names(surface) if name not in _FILTER_CONTROL_FIELDS)


def _model_resource_field(
    model: type[models.Model],
    name: str,
    *,
    relation_axis: DataRelationAxisMetadata | None,
    filterable: bool,
    sortable: bool,
    aggregatable: bool,
    groupable: bool,
    creatable: bool,
    updatable: bool,
    required_on_create: bool,
) -> DataResourceFieldMetadata:
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(
            f"resource metadata for {model._meta.label} declares unknown model field {name!r}."
        ) from error
    kind = resource_field_kind(field, has_relation_axis=relation_axis is not None)
    if kind in {"enum", "list"}:
        raise ImproperlyConfigured(
            f"resource metadata for {model._meta.label} cannot reconstruct {kind} field "
            f"{name!r} from the model; the node surface owns its enum values and item shape."
        )
    scalar = None if kind == "relation" else model_field_scalar(field)
    if scalar is None and kind == "scalar":
        raise ImproperlyConfigured(
            f"resource metadata for {model._meta.label} cannot classify model field "
            f"{name!r} ({field.__class__.__name__})."
        )
    return DataResourceFieldMetadata(
        name=name,
        kind=kind,
        scalar=scalar,
        values=(),
        widget=resource_field_widget(field, kind),
        filterable=filterable,
        sortable=sortable,
        aggregatable=aggregatable,
        groupable=groupable,
        creatable=creatable,
        updatable=updatable,
        required_on_create=required_on_create,
        relation_model_label=_relation_model_label(field, relation_axis),
        relation_label_axis=relation_axis.label_axis if relation_axis is not None else None,
    )


def _relation_model_label(
    field: models.Field[Any, Any] | None,
    relation_axis: DataRelationAxisMetadata | None,
) -> str | None:
    if relation_axis is not None:
        return relation_axis.model_label
    if field is None or not field.is_relation:
        return None
    remote_field = getattr(field, "remote_field", None)
    remote_model = getattr(remote_field, "model", None)
    meta = getattr(remote_model, "_meta", None)
    return str(meta.label) if meta is not None else None


def _wire_field_name(field: Any) -> str:
    """Return the GraphQL wire name the schema gives one Strawberry field."""

    return str(_WIRE_NAME_CONVERTER.get_graphql_name(field))


def _surface_field_type(surface: type | None, name: str) -> object | None:
    """Return the Strawberry type object for ``name`` when the surface exposes it."""

    if surface is None:
        return None
    definition = get_object_definition(surface)
    if definition is None:
        return None
    for field in definition.fields:
        if field.python_name == name:
            try:
                return field.type
            except NotImplementedError as exc:
                raise ImproperlyConfigured(
                    f"resource metadata for {surface_name(surface)} cannot resolve "
                    f"GraphQL type for field '{name}': {exc}"
                ) from exc
    return None


def _surface_field_scalar(
    *,
    surface: type,
    field_name: str,
    value: object | None,
    kind: str,
) -> str | None:
    """Return the scalar family exposed by a Strawberry surface field."""

    if kind in {"relation", "enum"} or value is None:
        return None
    if isinstance(value, StrawberryOptional):
        return _surface_field_scalar(
            surface=surface,
            field_name=field_name,
            value=value.of_type,
            kind=kind,
        )
    if kind == "list":
        return _surface_field_scalar_or_none(value)
    if isinstance(value, StrawberryEnumDefinition):
        return None
    scalar = _surface_field_scalar_or_none(value)
    if scalar is not None:
        return scalar
    raise ImproperlyConfigured(
        f"resource metadata for {surface_name(surface)} cannot classify "
        f"GraphQL scalar for field '{field_name}' ({_surface_type_name(value)})."
    )


def _surface_field_scalar_or_none(value: object | None) -> str | None:
    """Return a supported scalar family for ``value`` when it is scalar-like."""

    if value is None:
        return None
    if isinstance(value, StrawberryOptional):
        return _surface_field_scalar_or_none(value.of_type)
    if isinstance(value, StrawberryList):
        return _surface_field_scalar_or_none(value.of_type)
    if isinstance(value, StrawberryEnumDefinition):
        return None
    scalar_name = getattr(value, "__name__", None)
    if scalar_name in {"ID", "JSON"}:
        return str(scalar_name)
    if value is str:
        return "String"
    if value is bool:
        return "Boolean"
    if value is int:
        return "Int"
    if value is float or value is Decimal:
        return "Float"
    if value is datetime:
        return "DateTime"
    if value is date:
        return "Date"
    return None


def _surface_type_name(value: object | None) -> str:
    """Return a compact name for an unsupported Strawberry surface type."""

    if value is None:
        return "None"
    scalar_definition = getattr(value, "_scalar_definition", None)
    return str(
        getattr(value, "__name__", None)
        or getattr(value, "name", None)
        or getattr(scalar_definition, "name", None)
        or value.__class__.__name__
    )


def _strawberry_type_is_list(value: object) -> bool:
    """Return whether ``value`` is, or wraps, a Strawberry list type."""

    if isinstance(value, StrawberryList):
        return True
    if isinstance(value, StrawberryOptional):
        return _strawberry_type_is_list(value.of_type)
    return False


def _strawberry_type_is_enum(value: object | None) -> bool:
    """Return whether ``value`` is, or wraps, a Strawberry enum type."""

    if isinstance(value, StrawberryEnumDefinition):
        return True
    if isinstance(value, StrawberryOptional):
        return _strawberry_type_is_enum(value.of_type)
    return False


def _surface_enum_values(value: object | None) -> tuple[DataResourceEnumValueMetadata, ...]:
    """Return enum value metadata from the Strawberry enum surface."""

    definition = _strawberry_enum_definition(value)
    if definition is None:
        return ()
    return tuple(
        DataResourceEnumValueMetadata(
            value=str(enum_value.name),
            description=(
                str(enum_value.description)
                if enum_value.description is not None and str(enum_value.description).strip()
                else None
            ),
        )
        for enum_value in definition.values
    )


def _resource_enum_values(
    field: models.Field[Any, Any] | None,
    value: object | None,
) -> tuple[DataResourceEnumValueMetadata, ...]:
    """Return enum metadata, using impl registry labels for ImplClassField values."""

    values = _surface_enum_values(value)
    if not isinstance(field, ImplClassField) or not values:
        return values

    labels_by_key = {choice.key: choice.label for choice in field.impl_choices()}
    definition = _strawberry_enum_definition(value)
    if definition is None:
        return values
    labels_by_name = {
        str(enum_value.name): labels_by_key.get(str(enum_value.value))
        for enum_value in definition.values
    }
    return tuple(
        dataclasses.replace(item, description=labels_by_name.get(item.value) or item.description)
        for item in values
    )


def _strawberry_enum_definition(value: object | None) -> StrawberryEnumDefinition | None:
    """Return the unwrapped Strawberry enum definition for ``value``."""

    if isinstance(value, StrawberryEnumDefinition):
        return value
    if isinstance(value, StrawberryOptional):
        return _strawberry_enum_definition(value.of_type)
    return None


def _strawberry_type_is_object(value: object | None) -> bool:
    """Return whether ``value`` is, or will resolve as, a Strawberry object type."""

    if value is None:
        return False
    if isinstance(value, StrawberryOptional):
        return _strawberry_type_is_object(value.of_type)
    if isinstance(value, StrawberryList):
        return False
    try:
        if get_object_definition(value) is not None:
            return True
    except TypeError:
        pass
    return _surface_type_name(value) == "UNRESOLVED"


def _model_field_or_none(model: type[models.Model] | None, name: str) -> models.Field[Any, Any] | None:
    """Return a Django model field for ``name`` when one owns that GraphQL field."""

    if model is None:
        return None
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None
