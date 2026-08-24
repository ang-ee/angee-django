"""GraphQL/Django projection into neutral resource-field descriptions."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from angee.base.impl import ImplClassField
from angee.data import metadata as data_contract
from angee.data.field_classification import (
    RESOURCE_FIELD_KINDS as _RESOURCE_FIELD_KINDS,
)
from angee.data.field_classification import (
    RESOURCE_FIELD_SCALARS as _RESOURCE_FIELD_SCALARS,
)
from angee.data.field_classification import (
    RESOURCE_FIELD_WIDGETS as _RESOURCE_FIELD_WIDGETS,
)
from angee.data.field_classification import (
    is_archive_field,
    model_field_scalar,
    money_currency_field,
    resource_field_kind,
    resource_field_widget,
)
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryList, StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry.types.lazy_type import LazyType
from strawberry_django_hasura import SnakeNameConverter

from angee.graphql.introspection import surface_field_names, surface_name

_FILTER_CONTROL_FIELDS = frozenset({"AND", "OR", "NOT", "DISTINCT", "and", "or", "not", "distinct"})


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
        resource_wire_field_name(surface, name) or name for name in surface_field_names(surface) if name not in excluded
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


def require_resource_selection_path(
    surface: type | None,
    path: str,
    *,
    model_label: str,
) -> None:
    """Require a dotted GraphQL selection path to resolve through ``surface``.

    Each segment is matched by its actual wire name. Intermediate fields must
    project another Strawberry object; walking through a scalar/list or naming a
    missing field fails during metadata emission, before a generated detail query
    can carry the invalid selection.
    """

    current_surface: object | None = surface
    parts = path.split(".")
    for index, part in enumerate(parts):
        definition = get_object_definition(current_surface)
        field = (
            next(
                (
                    candidate
                    for candidate in definition.fields
                    if _wire_field_name(candidate) == part
                ),
                None,
            )
            if definition is not None
            else None
        )
        if field is None:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares subtitle selection "
                f"path {path!r} with unknown field {part!r}."
            )
        if index == len(parts) - 1:
            return
        try:
            next_surface, is_list = _selection_surface(field.type)
        except NotImplementedError as error:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} cannot resolve subtitle "
                f"selection path {path!r}: {error}"
            ) from error
        if is_list or get_object_definition(next_surface) is None:
            traversed = ".".join(parts[: index + 1])
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares subtitle selection "
                f"path {path!r} through non-object field {traversed!r}."
            )
        current_surface = next_surface


def model_resource_fields(
    model: type[models.Model],
    fields: tuple[str | data_contract.DataResourceFieldMetadata, ...],
    *,
    filter_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    aggregate_fields: tuple[str, ...] = (),
    group_by_fields: tuple[str, ...] = (),
    create_fields: tuple[str, ...] = (),
    update_fields: tuple[str, ...] = (),
    required_create_fields: tuple[str, ...] = (),
    relation_axes: tuple[data_contract.DataRelationAxisMetadata, ...] = (),
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
    """Return metadata for model fields exposed outside the node class.

    A caller may supply explicit metadata for a projected donor field whose
    shape the bare model cannot reconstruct (notably an enum). Ordinary string
    declarations retain the model-only fail-fast contract.
    """

    filterable = set(filter_fields)
    sortable = set(order_fields)
    aggregatable = set(aggregate_fields)
    groupable = set(group_by_fields)
    creatable = set(create_fields)
    updatable = set(update_fields)
    required_on_create = set(required_create_fields)
    relation_by_field = {axis.field: axis for axis in relation_axes}
    return tuple(
        (
            name
            if isinstance(name, data_contract.DataResourceFieldMetadata)
            else _model_resource_field(
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
        )
        for name in fields
    )


def input_wire_fields(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return declared input fields as GraphQL wire names."""

    excluded = set(exclude)
    return tuple(
        resource_wire_field_name(surface, name) or name for name in _input_fields(surface) if name not in excluded
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
    relation_axes: tuple[data_contract.DataRelationAxisMetadata, ...],
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
    """Return model resource field metadata from the declared node surface."""

    filterable = set(filter_fields)
    sortable = set(order_fields)
    aggregatable = set(aggregate_fields)
    groupable = set(group_by_fields)
    creatable = set(create_fields)
    updatable = set(update_fields)
    required_on_create = set(required_create_fields)
    relation_by_field = {axis.field: axis for axis in relation_axes}
    fields: list[data_contract.DataResourceFieldMetadata] = []
    for python_name in surface_field_names(node_type):
        name = resource_wire_field_name(node_type, python_name) or python_name
        axis = relation_by_field.get(name)
        model_field = _model_field_or_none(model, python_name)
        surface_type = _surface_field_type(node_type, python_name)
        is_object = _strawberry_type_is_object(surface_type)
        is_list = _strawberry_type_is_list(surface_type)
        is_enum = _strawberry_type_is_enum(surface_type)
        kind = resource_field_kind(
            model_field,
            has_relation_axis=axis is not None,
            is_list=is_list,
            is_enum=is_enum,
            is_object=is_object,
            projected_as_scalar=surface_type is not None and not is_object and not is_list and not is_enum,
        )
        scalar = _surface_field_scalar(
            surface=node_type,
            field_name=name,
            value=surface_type,
            kind=kind,
        )
        values = _resource_enum_values(model_field, surface_type) if kind == "enum" else ()
        fields.append(
            data_contract.DataResourceFieldMetadata(
                name=name,
                kind=kind,
                scalar=scalar,
                values=values,
                widget=_projected_widget(model_field, kind, scalar),
                filterable=name in filterable,
                sortable=name in sortable,
                aggregatable=name in aggregatable,
                groupable=name in groupable,
                creatable=name in creatable,
                updatable=name in updatable,
                required_on_create=name in required_on_create,
                archivable=is_archive_field(model_field),
                currency_field=money_currency_field(model_field),
                relation_model_label=_relation_model_label(model_field, axis),
                relation_label_axis=axis.label_axis if axis is not None else None,
                relation_object=kind == "relation" and is_object,
            )
        )
    return tuple(fields)


def require_unique_resource_fields(
    model_label: str,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[data_contract.DataResourceFieldMetadata, ...]:
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


def _validate_resource_field(model_label: str, field: data_contract.DataResourceFieldMetadata) -> None:
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


def _projected_widget(field: models.Field[Any, Any] | None, kind: str, scalar: str | None) -> str | None:
    """Return the rendered widget for a projected surface field.

    A plain ``ID`` scalar (a record's own public id) renders no widget; a scalar-id
    to-one relation (``kind == "scalar"``, ``scalar == "ID"``, a relation field)
    keeps its ``select`` picker widget so the relation still wires through.
    """

    if scalar == "ID" and not (field is not None and field.is_relation):
        return None
    return resource_field_widget(field, kind)


def _input_fields(surface: type | None) -> tuple[str, ...]:
    """Return declared input fields, excluding Strawberry-Django filter controls."""

    if surface is None:
        return ()
    return tuple(name for name in surface_field_names(surface) if name not in _FILTER_CONTROL_FIELDS)


def _model_resource_field(
    model: type[models.Model],
    name: str,
    *,
    relation_axis: data_contract.DataRelationAxisMetadata | None,
    filterable: bool,
    sortable: bool,
    aggregatable: bool,
    groupable: bool,
    creatable: bool,
    updatable: bool,
    required_on_create: bool,
) -> data_contract.DataResourceFieldMetadata:
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
    return data_contract.DataResourceFieldMetadata(
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
        archivable=is_archive_field(field),
        currency_field=money_currency_field(field),
        relation_model_label=_relation_model_label(field, relation_axis),
        relation_label_axis=relation_axis.label_axis if relation_axis is not None else None,
    )


def _relation_model_label(
    field: models.Field[Any, Any] | None,
    relation_axis: data_contract.DataRelationAxisMetadata | None,
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


def _selection_surface(value: object) -> tuple[object, bool]:
    """Return a selection field's unwrapped type and whether it crossed a list."""

    is_list = False
    while isinstance(value, StrawberryOptional | StrawberryList):
        if isinstance(value, StrawberryList):
            is_list = True
        value = value.of_type
    if isinstance(value, LazyType):
        value = value.resolve_type()
    return value, is_list


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
    if value is Decimal:
        return "Decimal"
    if value is float:
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


def _surface_enum_values(value: object | None) -> tuple[data_contract.DataResourceEnumValueMetadata, ...]:
    """Return enum value metadata from the Strawberry enum surface."""

    definition = _strawberry_enum_definition(value)
    if definition is None:
        return ()
    return tuple(
        data_contract.DataResourceEnumValueMetadata(
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
) -> tuple[data_contract.DataResourceEnumValueMetadata, ...]:
    """Return enum metadata, folding the field's choice labels into descriptions.

    An enum field's human labels live on the Django field — a ``StateField`` /
    ``TextChoices`` member's ``label`` (``ASSIGNED = "assigned", "Ready"``) or an
    ``ImplClassField`` registry label — not on the Strawberry enum value, so the
    frontend cannot derive "Ready" from the wire member name alone. Fold each label
    into the matching enum value's ``description`` so the metadata carries the
    authored label; a value the field does not label keeps any Strawberry-declared
    description.
    """

    values = _surface_enum_values(value)
    labels_by_value = _field_choice_labels(field)
    if not values or not labels_by_value:
        return values

    definition = _strawberry_enum_definition(value)
    if definition is None:
        return values
    labels_by_name = {
        str(enum_value.name): labels_by_value.get(str(enum_value.value)) for enum_value in definition.values
    }
    return tuple(
        dataclasses.replace(item, description=labels_by_name.get(item.value) or item.description) for item in values
    )


def _field_choice_labels(field: models.Field[Any, Any] | None) -> dict[str, str]:
    """Return a stored-value -> human-label map for an enum-backed field, or empty.

    An ``ImplClassField`` owns registry labels keyed by impl key; any other
    ``TextChoicesField`` (a ``StateField`` or a plain choices enum) carries its
    labels on the ``choices_enum`` members. A field with no enum choices yields an
    empty map, so a non-enum field folds nothing.
    """

    if isinstance(field, ImplClassField):
        return {str(choice.key): str(choice.label) for choice in field.impl_choices()}
    choices_enum = getattr(field, "choices_enum", None)
    if choices_enum is None:
        return {}
    return {str(member.value): str(member.label) for member in choices_enum}


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
