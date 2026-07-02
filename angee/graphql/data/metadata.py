"""Metadata for Angee model-backed data resource surfaces."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, TypeVar, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac.resources import model_resource_type
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryList, StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry.utils.str_converters import to_camel_case
from strawberry_django_hasura import SnakeNameConverter

from angee.base.fields import ImplClassField
from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
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
from angee.graphql.introspection import (
    FieldPathError,
    is_to_one_relation,
    require_field_for_path,
    surface_field_names,
    surface_name,
)

DATA_RESOURCE_METADATA_ATTR = "__angee_data_resource__"
"""Attribute attached to schema surfaces that contribute model resource metadata."""

_FILTER_CONTROL_FIELDS = frozenset({"AND", "OR", "NOT", "DISTINCT", "and", "or", "not", "distinct"})

_RESOURCE_CAPABILITY_ORDER = (
    "list",
    "detail",
    "aggregate",
    "groups",
    "filterEcho",
    "revisions",
    "create",
    "update",
    "delete",
    "deletePreview",
    "changes",
)

@dataclass(frozen=True, slots=True)
class DataRelationAxisMetadata:
    """Metadata for a relation group axis and its public identity lookup."""

    field: str
    model_label: str
    public_id_field: str
    label_axis: str | None = None


@dataclass(frozen=True, slots=True)
class DataGroupAliasMetadata:
    """Metadata for a display field that groups through another aggregate axis."""

    field: str
    aggregate_field: str
    aggregate_key: str


@dataclass(frozen=True, slots=True)
class DataGroupBucketFilterValueMapMetadata:
    """One backend-owned group bucket value rewrite for drill-down filters."""

    from_value: Any = dataclasses.field(metadata={"wire": "from"})
    to_value: Any = dataclasses.field(metadata={"wire": "to"})


@dataclass(frozen=True, slots=True)
class DataGroupBucketFilterMetadata:
    """Backend-owned predicate metadata for drilling into one group bucket."""

    kind: str
    field: str
    value_key: str | None = None
    range_key: str | None = None
    lookup: str | None = None
    null_lookup: str = "isNull"
    value_transform: str | None = None
    value_map: tuple[DataGroupBucketFilterValueMapMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class DataGroupExtractionMetadata:
    """One extraction supported by a group dimension, such as month or day."""

    name: str
    input: str
    key: str
    range_key: str | None = None
    filter: DataGroupBucketFilterMetadata | None = None


@dataclass(frozen=True, slots=True)
class DataGroupDimensionMetadata:
    """Backend-owned grouped bucket dimension metadata."""

    field: str
    input: str
    key: str
    kind: str = "column"
    scalar: str | None = None
    filter: DataGroupBucketFilterMetadata | None = None
    extractions: tuple[DataGroupExtractionMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class DataAggregateMeasureMetadata:
    """Aggregate measure selectable for one resource."""

    op: str
    field: str | None = None
    input: str | None = None


@dataclass(frozen=True, slots=True)
class DataDefaultSortMetadata:
    """One model default ordering term exposed through the resource order input."""

    field: str
    direction: str


@dataclass(frozen=True, slots=True)
class DataResourceRoots:
    """GraphQL wire root names emitted for one model data resource."""

    list_name: str | None = dataclasses.field(default=None, metadata={"wire": "list"})
    detail_name: str | None = dataclasses.field(default=None, metadata={"wire": "detail"})
    aggregate_name: str | None = dataclasses.field(default=None, metadata={"wire": "aggregate"})
    group_name: str | None = dataclasses.field(default=None, metadata={"wire": "groups"})
    create_name: str | None = dataclasses.field(default=None, metadata={"wire": "create"})
    update_name: str | None = dataclasses.field(default=None, metadata={"wire": "update"})
    delete_name: str | None = dataclasses.field(default=None, metadata={"wire": "delete"})
    delete_preview_name: str | None = dataclasses.field(default=None, metadata={"wire": "deletePreview"})
    revisions_name: str | None = dataclasses.field(default=None, metadata={"wire": "revisions"})
    changes_name: str | None = dataclasses.field(default=None, metadata={"wire": "changes"})

    def merge(self, left: DataResourceMetadata, right: DataResourceMetadata) -> DataResourceRoots:
        """Return root names merged with metadata-level collision checks."""

        return DataResourceRoots(
            **{
                field_def.name: _merge_value(
                    left,
                    right,
                    field_def.name,
                    getattr(self, field_def.name),
                    getattr(right.roots, field_def.name),
                )
                for field_def in dataclasses.fields(DataResourceRoots)
            }
        )


@dataclass(frozen=True, slots=True)
class DataResourceTypeNames:
    """GraphQL type names owned or referenced by one data resource."""

    query: str | None = None
    node: str | None = None
    filter: str | None = None
    order: str | None = None
    aggregate: str | None = None
    grouped: str | None = None
    group_key: str | None = None
    group_by_spec: str | None = None
    group_order: str | None = None
    having: str | None = None
    create_input: str | None = None
    update_input: str | None = None
    delete_payload: str | None = None
    revision: str | None = None

    def merge(self, left: DataResourceMetadata, right: DataResourceMetadata) -> DataResourceTypeNames:
        """Return type names merged with metadata-level collision checks."""

        return DataResourceTypeNames(
            **{
                field_def.name: _merge_value(
                    left,
                    right,
                    field_def.name,
                    getattr(self, field_def.name),
                    getattr(right.type_names, field_def.name),
                )
                for field_def in dataclasses.fields(DataResourceTypeNames)
            }
        )


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


@dataclass(frozen=True, slots=True)
class DataResourceMetadata:
    """Internal metadata for one Angee model data resource."""

    model: type[models.Model] | None = dataclasses.field(metadata={"wire": False})
    model_label: str
    resource_type: str | None
    app_label: str
    model_name: str
    public_id_field: str
    roots: DataResourceRoots
    type_names: DataResourceTypeNames
    row_model: str = "server"
    capabilities: tuple[str, ...] = ()
    fields: tuple[DataResourceFieldMetadata, ...] = ()
    filter_fields: tuple[str, ...] = ()
    order_fields: tuple[str, ...] = ()
    aggregate_fields: tuple[str, ...] = ()
    group_by_fields: tuple[str, ...] = ()
    group_dimensions: tuple[DataGroupDimensionMetadata, ...] = ()
    aggregate_measures: tuple[DataAggregateMeasureMetadata, ...] = ()
    default_measures: tuple[DataAggregateMeasureMetadata, ...] = ()
    default_sort: tuple[DataDefaultSortMetadata, ...] = ()
    create_fields: tuple[str, ...] = ()
    update_fields: tuple[str, ...] = ()
    required_create_fields: tuple[str, ...] = ()
    revision_fields: tuple[str, ...] = ()
    relation_axes: tuple[DataRelationAxisMetadata, ...] = ()
    group_aliases: tuple[DataGroupAliasMetadata, ...] = ()
    node_type: type | None = dataclasses.field(default=None, metadata={"wire": False})
    filter_type: type | None = dataclasses.field(default=None, metadata={"wire": False})
    order_type: type | None = dataclasses.field(default=None, metadata={"wire": False})

    def merge(self, other: DataResourceMetadata) -> DataResourceMetadata:
        """Return this resource contribution merged with another same-model contribution."""

        if self.model is not other.model:
            left_owner = self.model._meta.label if self.model is not None else self.model_label
            right_owner = other.model._meta.label if other.model is not None else other.model_label
            raise ImproperlyConfigured(
                f"resource metadata model label '{self.model_label}' is contributed by both "
                f"{left_owner} and {right_owner}."
            )
        return DataResourceMetadata(
            model=self.model,
            model_label=self.model_label,
            resource_type=self.resource_type or other.resource_type,
            app_label=self.app_label,
            model_name=self.model_name,
            public_id_field=cast(
                str,
                _merge_value(self, other, "public_id_field", self.public_id_field, other.public_id_field),
            ),
            roots=self.roots.merge(self, other),
            type_names=self.type_names.merge(self, other),
            row_model=_merge_row_model(self, other),
            capabilities=_merge_capabilities(self.capabilities, other.capabilities),
            fields=_merge_resource_fields(self.fields, other.fields),
            filter_fields=self.filter_fields or other.filter_fields,
            order_fields=self.order_fields or other.order_fields,
            aggregate_fields=self.aggregate_fields or other.aggregate_fields,
            group_by_fields=self.group_by_fields or other.group_by_fields,
            group_dimensions=self.group_dimensions or other.group_dimensions,
            aggregate_measures=self.aggregate_measures or other.aggregate_measures,
            default_measures=self.default_measures or other.default_measures,
            default_sort=self.default_sort or other.default_sort,
            create_fields=self.create_fields or other.create_fields,
            update_fields=self.update_fields or other.update_fields,
            required_create_fields=self.required_create_fields or other.required_create_fields,
            revision_fields=self.revision_fields or other.revision_fields,
            relation_axes=self.relation_axes or other.relation_axes,
            group_aliases=self.group_aliases or other.group_aliases,
            node_type=self.node_type or other.node_type,
            filter_type=self.filter_type or other.filter_type,
            order_type=self.order_type or other.order_type,
        )

    def as_wire(self, *, schema_name: str) -> dict[str, object]:
        """Return this resource metadata in JSON-safe frontend wire shape."""

        return {"schemaName": schema_name, **_wire_dataclass(self)}


def data_resource_metadata(surface: object) -> tuple[DataResourceMetadata, ...]:
    """Return model resource metadata attached to ``surface``."""

    metadata = getattr(surface, DATA_RESOURCE_METADATA_ATTR, None)
    if metadata is None:
        return ()
    if isinstance(metadata, DataResourceMetadata):
        return (metadata,)
    if isinstance(metadata, tuple) and all(isinstance(item, DataResourceMetadata) for item in metadata):
        return metadata
    return ()


def resource_wire_field_name(surface: type | None, name: str | None) -> str | None:
    """Return the actual GraphQL wire field name for a Strawberry surface field."""

    return _surface_wire_field_name(surface, name)


def resource_wire_field_names(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return all declared GraphQL wire field names for a Strawberry surface."""

    if surface is None:
        return ()
    excluded = set(exclude)
    return tuple(
        _surface_wire_field_name(surface, name) or name
        for name in surface_field_names(surface)
        if name not in excluded
    )


def resource_type_name(surface: type | None) -> str | None:
    """Return the GraphQL type name for ``surface`` when present."""

    return _optional_type_name(surface)


def make_data_resource_metadata(
    *,
    model: type[models.Model] | None = None,
    roots: DataResourceRoots,
    type_names: DataResourceTypeNames,
    capabilities: tuple[str, ...],
    node_type: type | None = None,
    filter_type: type | None = None,
    order_type: type | None = None,
    filter_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    aggregate_fields: tuple[str, ...] = (),
    group_by_fields: tuple[str, ...] = (),
    group_dimensions: tuple[DataGroupDimensionMetadata, ...] = (),
    aggregate_measures: tuple[DataAggregateMeasureMetadata, ...] = (),
    default_measures: tuple[DataAggregateMeasureMetadata, ...] = (),
    default_sort: tuple[DataDefaultSortMetadata, ...] = (),
    create_input_type: type | None = None,
    update_input_type: type | None = None,
    create_fields: tuple[str, ...] = (),
    update_fields: tuple[str, ...] = (),
    required_create_fields: tuple[str, ...] = (),
    revision_fields: tuple[str, ...] = (),
    relation_axes: tuple[DataRelationAxisMetadata, ...] = (),
    group_aliases: tuple[DataGroupAliasMetadata, ...] = (),
    fields: tuple[DataResourceFieldMetadata, ...] = (),
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    row_model: str = "server",
) -> DataResourceMetadata:
    """Build one resource metadata contribution from an owning schema surface.

    ``model`` is the owning Django model for a model-backed resource. A computed
    (non-model) resource passes ``model=None`` and a dotted ``model_label`` (e.g.
    ``"platform.addon"``); the model is only ever used internally (it is
    ``{"wire": False}``), so the wire payload is identical either way.

    ``row_model`` is the client/server boundary signal the frontend reads
    (``"server"`` by default — Hasura ``where``/``order_by``/``limit`` + the
    ``_groups`` aggregate; ``"client"`` for a small computed set that fetches once
    and filters/sorts/paginates/groups in the browser).
    """

    if model_label is not None:
        exposed_model_label = model_label
    elif model is not None:
        exposed_model_label = model._meta.label
    else:
        raise ImproperlyConfigured("make_data_resource_metadata requires model_label when model is None.")
    app_label, model_name = _model_label_parts(exposed_model_label, model)
    filter_fields = _require_unique(exposed_model_label, "filter field", filter_fields)
    order_fields = _require_unique(exposed_model_label, "order field", order_fields)
    aggregate_fields = _require_unique(exposed_model_label, "aggregate field", aggregate_fields)
    group_by_fields = _require_unique(exposed_model_label, "group axis", group_by_fields)
    if model is not None and roots.group_name is not None and not relation_axes:
        relation_axes = _relation_axes(model, group_by_fields)
    if model is not None and order_fields and not default_sort:
        default_sort = _default_sort(model, order_fields)
    active_create_fields = _require_unique(
        exposed_model_label,
        "create field",
        create_fields or _input_wire_fields(create_input_type, exclude=("id",)),
    )
    active_update_fields = _require_unique(
        exposed_model_label,
        "update field",
        update_fields or _input_wire_fields(update_input_type, exclude=("id",)),
    )
    active_required_create_fields = _require_unique(
        exposed_model_label,
        "required create field",
        required_create_fields or _required_input_wire_fields(create_input_type),
    )
    revision_fields = _require_unique(exposed_model_label, "revision field", revision_fields)
    declared_fields = _require_unique_resource_fields(exposed_model_label, fields)
    generated_fields = (
        _resource_fields(
            node_type,
            model,
            filter_fields=filter_fields,
            order_fields=order_fields,
            aggregate_fields=aggregate_fields,
            group_by_fields=group_by_fields,
            create_fields=active_create_fields,
            update_fields=active_update_fields,
            required_create_fields=active_required_create_fields,
            relation_axes=relation_axes,
        )
        if node_type is not None
        else ()
    )
    active_fields = (
        _merge_resource_fields(generated_fields, declared_fields)
        if declared_fields
        else generated_fields
    )
    active_fields = _require_unique_resource_fields(exposed_model_label, active_fields)
    return DataResourceMetadata(
        model=model,
        model_label=exposed_model_label,
        resource_type=model_resource_type(model) if model is not None else None,
        app_label=app_label,
        model_name=model_name,
        public_id_field=public_id_field,
        roots=roots,
        type_names=type_names,
        row_model=row_model,
        capabilities=capabilities,
        fields=active_fields,
        filter_fields=filter_fields,
        order_fields=order_fields,
        aggregate_fields=aggregate_fields,
        group_by_fields=group_by_fields,
        group_dimensions=group_dimensions,
        aggregate_measures=aggregate_measures,
        default_measures=default_measures,
        default_sort=default_sort,
        create_fields=active_create_fields,
        update_fields=active_update_fields,
        required_create_fields=active_required_create_fields,
        revision_fields=revision_fields,
        relation_axes=relation_axes,
        group_aliases=group_aliases,
        node_type=node_type,
        filter_type=filter_type,
        order_type=order_type,
    )


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
    """Return resource metadata for model fields exposed outside the node class.

    Same-row model extensions can expose fields through Strawberry type
    extensions. The node class that owns the Hasura resource cannot import those
    downstream extension classes, so this builds matching field metadata directly
    from the composed Django model fields.
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


_SurfaceT = TypeVar("_SurfaceT")


def attach_data_resource_metadata(
    surface: type[_SurfaceT],
    metadata: DataResourceMetadata,
) -> type[_SurfaceT]:
    """Attach model resource metadata to a generated Strawberry surface."""

    existing = data_resource_metadata(surface)
    setattr(surface, DATA_RESOURCE_METADATA_ATTR, existing + (metadata,))
    return surface


def merge_data_resources(
    metadata: tuple[DataResourceMetadata, ...],
) -> tuple[DataResourceMetadata, ...]:
    """Merge per-surface resource contributions into one resource per model."""

    merged: dict[str, DataResourceMetadata] = {}
    for item in metadata:
        existing = merged.get(item.model_label)
        merged[item.model_label] = item if existing is None else existing.merge(item)
    return tuple(merged.values())


def serialize_data_resources(
    metadata: tuple[DataResourceMetadata, ...],
    *,
    schema_name: str,
) -> list[dict[str, object]]:
    """Return a JSON-safe schema-extension payload for resource metadata."""

    return [item.as_wire(schema_name=schema_name) for item in metadata]


def _wire_dataclass(instance: Any) -> dict[str, object]:
    """Serialize one metadata dataclass through its own declared wire shape.

    Each dataclass owns its wire mapping: a field serializes under its
    ``_metadata_key`` (camelCase) name unless it declares a ``wire`` key in field
    metadata, and fields marked ``{"wire": False}`` (the Python type handles) are
    omitted.
    """

    payload: dict[str, object] = {}
    for field_def in dataclasses.fields(instance):
        wire = field_def.metadata.get("wire", True)
        if wire is False:
            continue
        key = wire if isinstance(wire, str) else _metadata_key(field_def.name)
        payload[key] = _wire_value(getattr(instance, field_def.name))
    return payload


def _wire_value(value: object) -> object:
    """Return a JSON-safe wire value for one metadata field."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _wire_dataclass(value)
    if isinstance(value, (tuple, list)):
        return [_wire_value(item) for item in value]
    return value


def _merge_row_model(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
) -> str:
    """Return one row-model signal, rejecting conflicting contributions."""

    if left.row_model != right.row_model:
        raise ImproperlyConfigured(
            f"resource metadata for {left.model_label} has conflicting row_model: "
            f"{left.row_model!r} and {right.row_model!r}."
        )
    return left.row_model


def _merge_value(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
    name: str,
    left_value: str | None,
    right_value: str | None,
) -> str | None:
    """Return one metadata value, rejecting conflicting contributions."""

    if left_value is not None and right_value is not None and left_value != right_value:
        raise ImproperlyConfigured(
            f"resource metadata for {left.model_label} has conflicting {name}: {left_value!r} and {right_value!r}."
        )
    return left_value if left_value is not None else right_value


def _merge_capabilities(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Return deterministic capability names from both resource contributions."""

    names = {*left, *right}
    ordered = [name for name in _RESOURCE_CAPABILITY_ORDER if name in names]
    ordered.extend(sorted(names - set(_RESOURCE_CAPABILITY_ORDER)))
    return tuple(ordered)


def _require_unique(
    model_label: str,
    purpose: str,
    values: tuple[str, ...],
) -> tuple[str, ...]:
    """Return ``values`` after rejecting duplicate declarations."""

    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ImproperlyConfigured(f"resource metadata for {model_label} declares duplicate {purpose} '{value}'.")
        seen.add(value)
    return values


def _require_unique_resource_fields(
    model_label: str,
    fields: tuple[DataResourceFieldMetadata, ...],
) -> tuple[DataResourceFieldMetadata, ...]:
    """Return resource field metadata after rejecting duplicate field names."""

    _require_unique(model_label, "resource field", tuple(field.name for field in fields))
    for field in fields:
        _validate_resource_field(model_label, field)
    return fields


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


def _merge_resource_fields(
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


def _input_fields(surface: type | None) -> tuple[str, ...]:
    """Return declared input fields, excluding Strawberry-Django filter controls."""

    if surface is None:
        return ()
    return tuple(name for name in surface_field_names(surface) if name not in _FILTER_CONTROL_FIELDS)


def _input_wire_fields(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return declared input fields as GraphQL wire names."""

    excluded = set(exclude)
    return tuple(
        _surface_wire_field_name(surface, name) or name
        for name in _input_fields(surface)
        if name not in excluded
    )


def _required_input_wire_fields(surface: type | None) -> tuple[str, ...]:
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


def _resource_fields(
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
        name = _surface_wire_field_name(node_type, python_name) or python_name
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
        # `model_field_scalar` never yields "ID", so unlike the surface path the
        # widget is always derived from the field kind here.
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


# The schema is built with ``hasura_config()`` (``angee/graphql/schema.py``); its
# ``SnakeNameConverter`` owns the python-name -> wire-name rule, keeping snake_case
# verbatim unless a field pins an explicit ``graphql_name``. The metadata the
# frontend codegen reads must name every field exactly as the schema does, so it
# asks the same converter instead of re-deriving the rule — re-deriving it as
# camelCase is what made an unpinned field (the revision query) drift from its
# snake_case schema name.
_WIRE_NAME_CONVERTER = SnakeNameConverter()


def _wire_field_name(field: Any) -> str:
    """Return the GraphQL wire name the schema gives one Strawberry field."""

    return str(_WIRE_NAME_CONVERTER.get_graphql_name(field))


def _surface_wire_field_name(surface: type | None, name: str | None) -> str | None:
    """Return the actual GraphQL wire field name for a Strawberry field."""

    if surface is None or name is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        for field in definition.fields:
            if field.python_name == name:
                return _wire_field_name(field)
    return name


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


def _metadata_key(name: str) -> str:
    """Return the camelCase JSON key for one metadata-envelope dataclass field.

    The frontend ``DataResourceMetadata`` contract keys its objects in camelCase
    (``revisionFields``, ``typeNames``); that is the metadata envelope's own
    naming, distinct from the snake_case GraphQL wire field names above.
    """

    return to_camel_case(name)


def _model_field_or_none(model: type[models.Model] | None, name: str) -> models.Field[Any, Any] | None:
    """Return a Django model field for ``name`` when one owns that GraphQL field."""

    if model is None:
        return None
    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _default_sort(
    model: type[models.Model],
    order_fields: tuple[str, ...],
) -> tuple[DataDefaultSortMetadata, ...]:
    """Return model default ordering terms exposed by the order input."""

    orderable = set(order_fields)
    sorts: list[DataDefaultSortMetadata] = []
    for term in model._meta.ordering:
        if not isinstance(term, str):
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} cannot expose non-string default ordering {term!r}."
            )
        if term == "?":
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} cannot expose random default ordering."
            )
        field = term[1:] if term.startswith("-") else term
        if field not in orderable:
            continue
        _require_model_field_for_path(model, field, purpose="default ordering")
        sorts.append(
            DataDefaultSortMetadata(
                field=field,
                direction="DESC" if term.startswith("-") else "ASC",
            )
        )
    return tuple(sorts)


def _relation_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[DataRelationAxisMetadata, ...]:
    """Return direct FK group axes with their related model and optional label axis."""

    label_axes = _relation_label_axes(model, group_by_fields)
    relation_axes: list[DataRelationAxisMetadata] = []
    for path in group_by_fields:
        if "__" in path:
            continue
        try:
            field = model._meta.get_field(path)
        except FieldDoesNotExist:
            continue
        if not is_to_one_relation(field):
            continue
        remote_field = getattr(field, "remote_field", None)
        related_model = getattr(remote_field, "model", None)
        if related_model is None:
            continue
        relation_axes.append(
            DataRelationAxisMetadata(
                field=path,
                model_label=related_model._meta.label,
                public_id_field=PUBLIC_ID_FIELD_NAME,
                label_axis=label_axes.get(path),
            )
        )
    return tuple(relation_axes)


def _relation_label_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> dict[str, str]:
    """Return relation label axes keyed by their direct relation axis."""

    direct_axes = {path for path in group_by_fields if "__" not in path}
    label_axes: dict[str, str] = {}
    for path in group_by_fields:
        if "__" not in path:
            continue
        relation, _leaf = path.split("__", 1)
        try:
            field = model._meta.get_field(relation)
        except FieldDoesNotExist:
            continue
        if not is_to_one_relation(field):
            continue
        if relation not in direct_axes:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} relation label axis '{path}' "
                f"requires matching direct relation group axis '{relation}'."
            )
        existing = label_axes.get(relation)
        if existing is not None and existing != path:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} relation group axis '{relation}' "
                f"declares multiple label axes: '{existing}' and '{path}'."
            )
        label_axes[relation] = path
    return label_axes


def _require_model_field_for_path(
    model: type[models.Model],
    path: str,
    *,
    purpose: str,
) -> models.Field[Any, Any]:
    """Return a concrete model field for ``path`` or fail at metadata emission."""

    try:
        return require_field_for_path(model, path)
    except FieldPathError as error:
        if error.to_many:
            raise ImproperlyConfigured(
                f"resource metadata for {model._meta.label} declares unsupported to-many {purpose} field path '{path}'."
            ) from None
        raise ImproperlyConfigured(
            f"resource metadata for {model._meta.label} declares unknown {purpose} field path '{path}'."
        ) from None


def _optional_type_name(surface: type | None) -> str | None:
    """Return a Strawberry type name for ``surface`` when present."""

    if surface is None:
        return None
    return _type_name(surface)


def _type_name(surface: type) -> str:
    """Return the GraphQL type name for a Strawberry surface."""

    definition = get_object_definition(surface)
    if definition is not None:
        return str(definition.name)
    definition = getattr(surface, "__strawberry_definition__", None)
    if definition is not None:
        return str(definition.name)
    return surface_name(surface)


def _model_label_parts(
    model_label: str,
    model: type[models.Model] | None,
) -> tuple[str, str]:
    """Return metadata app/model names for a public model label.

    A computed resource has no model; its dotted ``app.model`` label is split
    directly. A model-backed resource whose label equals ``model._meta.label``
    reuses the model's own app/model names.
    """

    if model is not None and model_label == model._meta.label:
        return model._meta.app_label, model._meta.model_name
    app_label, object_name = model_label.split(".", 1)
    return app_label, object_name.lower()
