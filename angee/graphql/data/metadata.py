"""Metadata for Angee model-backed data resource surfaces."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from strawberry.types import get_object_definition
from strawberry.types.base import StrawberryList, StrawberryOptional
from strawberry.types.enum import StrawberryEnumDefinition
from strawberry.utils.str_converters import to_camel_case

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.introspection import surface_field_names, surface_name

DATA_QUERY_METADATA_ATTR = "__angee_data_query__"
"""Attribute attached to generated data-query root classes."""

DATA_RESOURCE_METADATA_ATTR = "__angee_data_resource__"
"""Attribute attached to schema surfaces that contribute model resource metadata."""

_FILTER_CONTROL_FIELDS = frozenset({"AND", "OR", "NOT", "DISTINCT", "and", "or", "not", "distinct"})
_GROUP_EXTRACTIONS = (
    ("year", "YEAR", "year", "year_range"),
    ("quarter", "QUARTER", "quarter", "quarter_range"),
    ("month", "MONTH", "month", "month_range"),
    ("week", "WEEK", "week", "week_range"),
    ("day", "DAY", "day", "day_range"),
    ("hour", "HOUR", "hour", "hour_range"),
    ("minute", "MINUTE", "minute", "minute_range"),
    ("second", "SECOND", "second", "second_range"),
    ("year_number", "YEAR_NUMBER", "year_number", None),
    ("quarter_number", "QUARTER_NUMBER", "quarter_number", None),
    ("month_number", "MONTH_NUMBER", "month_number", None),
    ("iso_week_number", "ISO_WEEK_NUMBER", "iso_week_number", None),
    ("day_of_year", "DAY_OF_YEAR", "day_of_year", None),
    ("day_of_month", "DAY_OF_MONTH", "day_of_month", None),
    ("day_of_week", "DAY_OF_WEEK", "day_of_week", None),
    ("hour_number", "HOUR_NUMBER", "hour_number", None),
    ("minute_number", "MINUTE_NUMBER", "minute_number", None),
    ("second_number", "SECOND_NUMBER", "second_number", None),
)
_FRONTEND_MEASURE_OPS = ("sum", "avg", "min", "max")

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

_RESOURCE_ROOT_FIELDS = (
    "list_name",
    "detail_name",
    "aggregate_name",
    "group_name",
    "create_name",
    "update_name",
    "delete_name",
    "delete_preview_name",
    "revisions_name",
    "changes_name",
)

_RESOURCE_TYPE_FIELDS = (
    "query",
    "node",
    "filter",
    "order",
    "aggregate",
    "grouped",
    "group_key",
    "group_by_spec",
    "group_order",
    "having",
    "create_input",
    "update_input",
    "delete_payload",
    "revision",
)

_RESOURCE_FIELD_KINDS = frozenset({"scalar", "enum", "relation", "list"})
_RESOURCE_FIELD_SCALARS = frozenset({"ID", "String", "Boolean", "Int", "Float", "DateTime", "Date", "JSON"})
_RESOURCE_FIELD_WIDGETS = frozenset(
    {"select", "many2one", "tagInput", "switch", "integer", "float", "datetime", "date", "json"}
)


@dataclass(frozen=True, slots=True)
class DataQueryRoots:
    """GraphQL root field names emitted for one model data surface."""

    list_name: str | None = None
    detail_name: str | None = None
    aggregate_name: str | None = None
    group_name: str | None = None


@dataclass(frozen=True, slots=True)
class DataQueryTypeNames:
    """GraphQL type names owned or referenced by one data query surface."""

    query: str
    node: str
    filter: str | None = None
    order: str | None = None
    aggregate: str | None = None
    grouped: str | None = None
    group_key: str | None = None
    group_by_spec: str | None = None
    group_order: str | None = None
    having: str | None = None


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
class DataGroupExtractionMetadata:
    """One extraction supported by a group dimension, such as month or day."""

    name: str
    input: str
    key: str
    range_key: str | None = None


@dataclass(frozen=True, slots=True)
class DataGroupDimensionMetadata:
    """Backend-owned grouped bucket dimension metadata."""

    field: str
    input: str
    key: str
    kind: str = "column"
    scalar: str | None = None
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

    list_name: str | None = None
    detail_name: str | None = None
    aggregate_name: str | None = None
    group_name: str | None = None
    create_name: str | None = None
    update_name: str | None = None
    delete_name: str | None = None
    delete_preview_name: str | None = None
    revisions_name: str | None = None
    changes_name: str | None = None


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


@dataclass(frozen=True, slots=True)
class DataResourceFieldMetadata:
    """Field capability metadata emitted for one model resource field."""

    name: str
    kind: str
    scalar: str | None = None
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
class DataQueryMetadata:
    """Internal metadata for one Angee model data query surface."""

    query_type: type
    node_type: type
    model: type[models.Model]
    model_label: str
    app_label: str
    model_name: str
    public_id_field: str
    roots: DataQueryRoots
    type_names: DataQueryTypeNames
    capabilities: tuple[str, ...]
    filter_fields: tuple[str, ...]
    order_fields: tuple[str, ...]
    aggregate_fields: tuple[str, ...]
    group_by_fields: tuple[str, ...]
    group_dimensions: tuple[DataGroupDimensionMetadata, ...]
    aggregate_measures: tuple[DataAggregateMeasureMetadata, ...]
    default_measures: tuple[DataAggregateMeasureMetadata, ...]
    default_sort: tuple[DataDefaultSortMetadata, ...]
    relation_axes: tuple[DataRelationAxisMetadata, ...]
    group_aliases: tuple[DataGroupAliasMetadata, ...]
    filter_type: type | None = None
    order_type: type | None = None


@dataclass(frozen=True, slots=True)
class DataResourceMetadata:
    """Internal metadata for one Angee model data resource."""

    model: type[models.Model]
    model_label: str
    app_label: str
    model_name: str
    public_id_field: str
    roots: DataResourceRoots
    type_names: DataResourceTypeNames
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
    node_type: type | None = None
    filter_type: type | None = None
    order_type: type | None = None


def data_query_metadata(surface: object) -> tuple[DataQueryMetadata, ...]:
    """Return data-query metadata attached to ``surface``."""

    metadata = getattr(surface, DATA_QUERY_METADATA_ATTR, None)
    if metadata is None:
        return ()
    if isinstance(metadata, DataQueryMetadata):
        return (metadata,)
    if isinstance(metadata, tuple) and all(isinstance(item, DataQueryMetadata) for item in metadata):
        return metadata
    return ()


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
        _surface_wire_field_name(surface, name) or _wire_name(name)
        for name in surface_field_names(surface)
        if name not in excluded
    )


def resource_type_name(surface: type | None) -> str | None:
    """Return the GraphQL type name for ``surface`` when present."""

    return _optional_type_name(surface)


def make_data_query_metadata(
    *,
    query_type: type,
    node_type: type,
    model: type[models.Model],
    roots: DataQueryRoots,
    filter_type: type | None,
    order_type: type | None,
    aggregate_fields: tuple[str, ...],
    group_by_fields: tuple[str, ...],
    group_aliases: tuple[tuple[str, str], ...],
    enable_filter_echo: bool,
    aggregate_type: type | None = None,
    grouped_type: type | None = None,
    group_key_type: type | None = None,
    group_by_spec_type: type | None = None,
    group_order_input_type: type | None = None,
    having_type: type | None = None,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
) -> DataQueryMetadata:
    """Build metadata for a generated data-query class."""

    exposed_model_label = model_label or model._meta.label
    app_label, model_name = _model_label_parts(exposed_model_label, model)
    filter_fields = _require_unique(
        exposed_model_label,
        "filter field",
        _input_fields(filter_type),
    )
    order_fields = _require_unique(
        exposed_model_label,
        "order field",
        _input_fields(order_type),
    )
    aggregate_fields = _require_unique(
        exposed_model_label,
        "aggregate field",
        aggregate_fields,
    )
    group_by_fields = _require_unique(
        exposed_model_label,
        "group axis",
        group_by_fields,
    )
    return DataQueryMetadata(
        query_type=query_type,
        node_type=node_type,
        model=model,
        model_label=exposed_model_label,
        app_label=app_label,
        model_name=model_name,
        public_id_field=public_id_field,
        roots=roots,
        type_names=DataQueryTypeNames(
            query=_type_name(query_type),
            node=_type_name(node_type),
            filter=_optional_type_name(filter_type),
            order=_optional_type_name(order_type),
            aggregate=_optional_type_name(aggregate_type),
            grouped=_optional_type_name(grouped_type),
            group_key=_optional_type_name(group_key_type),
            group_by_spec=_optional_type_name(group_by_spec_type),
            group_order=_optional_type_name(group_order_input_type),
            having=_optional_type_name(having_type),
        ),
        capabilities=_capabilities(roots, enable_filter_echo),
        filter_fields=filter_fields,
        order_fields=order_fields,
        aggregate_fields=aggregate_fields,
        group_by_fields=group_by_fields,
        group_dimensions=_group_dimensions(model, group_by_fields),
        aggregate_measures=_aggregate_measures(model, aggregate_fields),
        default_measures=(DataAggregateMeasureMetadata(op="count"),),
        default_sort=_default_sort(model, order_fields),
        relation_axes=_relation_axes(model, group_by_fields),
        group_aliases=_group_aliases(node_type, model, group_by_fields, group_aliases),
        filter_type=filter_type,
        order_type=order_type,
    )


def make_data_resource_metadata(
    *,
    model: type[models.Model],
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
) -> DataResourceMetadata:
    """Build one resource metadata contribution from an owning schema surface."""

    exposed_model_label = model_label or model._meta.label
    app_label, model_name = _model_label_parts(exposed_model_label, model)
    filter_fields = _require_unique(exposed_model_label, "filter field", filter_fields)
    order_fields = _require_unique(exposed_model_label, "order field", order_fields)
    aggregate_fields = _require_unique(exposed_model_label, "aggregate field", aggregate_fields)
    group_by_fields = _require_unique(exposed_model_label, "group axis", group_by_fields)
    if roots.aggregate_name is not None:
        if not aggregate_measures:
            aggregate_measures = _aggregate_measures(model, aggregate_fields)
        if not default_measures:
            default_measures = (DataAggregateMeasureMetadata(op="count"),)
    if roots.group_name is not None and not group_dimensions:
        group_dimensions = _group_dimensions(model, group_by_fields)
    if roots.group_name is not None and not relation_axes:
        relation_axes = _relation_axes(model, group_by_fields)
    if order_fields and not default_sort:
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
    active_fields = _require_unique_resource_fields(exposed_model_label, fields)
    if not active_fields and node_type is not None:
        active_fields = _resource_fields(
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
    active_fields = _require_unique_resource_fields(exposed_model_label, active_fields)
    return DataResourceMetadata(
        model=model,
        model_label=exposed_model_label,
        app_label=app_label,
        model_name=model_name,
        public_id_field=public_id_field,
        roots=roots,
        type_names=type_names,
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


def data_resource_from_data_query(metadata: DataQueryMetadata) -> DataResourceMetadata:
    """Return the resource-level projection of a data-query contribution."""

    filter_fields = _surface_wire_field_names(metadata.filter_type, metadata.filter_fields)
    order_fields = _surface_wire_field_names(metadata.order_type, metadata.order_fields)
    aggregate_fields = _wire_paths(metadata.aggregate_fields)
    group_by_fields = _wire_paths(metadata.group_by_fields)
    group_dimensions = _wire_group_dimensions(metadata.group_dimensions)
    aggregate_measures = _wire_aggregate_measures(metadata.aggregate_measures)
    default_measures = _wire_aggregate_measures(metadata.default_measures)
    default_sort = _wire_default_sort(metadata.default_sort)
    relation_axes = _wire_relation_axes(metadata.relation_axes)
    return make_data_resource_metadata(
        model=metadata.model,
        model_label=metadata.model_label,
        public_id_field=metadata.public_id_field,
        node_type=metadata.node_type,
        filter_type=metadata.filter_type,
        order_type=metadata.order_type,
        roots=DataResourceRoots(
            list_name=_surface_wire_field_name(metadata.query_type, metadata.roots.list_name),
            detail_name=_surface_wire_field_name(metadata.query_type, metadata.roots.detail_name),
            aggregate_name=_surface_wire_field_name(metadata.query_type, metadata.roots.aggregate_name),
            group_name=_surface_wire_field_name(metadata.query_type, metadata.roots.group_name),
        ),
        type_names=DataResourceTypeNames(
            query=metadata.type_names.query,
            node=metadata.type_names.node,
            filter=metadata.type_names.filter,
            order=metadata.type_names.order,
            aggregate=metadata.type_names.aggregate,
            grouped=metadata.type_names.grouped,
            group_key=metadata.type_names.group_key,
            group_by_spec=metadata.type_names.group_by_spec,
            group_order=metadata.type_names.group_order,
            having=metadata.type_names.having,
        ),
        capabilities=metadata.capabilities,
        filter_fields=filter_fields,
        order_fields=order_fields,
        aggregate_fields=aggregate_fields,
        group_by_fields=group_by_fields,
        group_dimensions=group_dimensions,
        aggregate_measures=aggregate_measures,
        default_measures=default_measures,
        default_sort=default_sort,
        relation_axes=relation_axes,
        group_aliases=_wire_group_aliases(metadata.group_aliases),
    )


def attach_data_query_metadata(
    query_type: type,
    metadata: DataQueryMetadata,
) -> type:
    """Attach data-query metadata to a generated Strawberry query class."""

    setattr(query_type, DATA_QUERY_METADATA_ATTR, metadata)
    return attach_data_resource_metadata(query_type, data_resource_from_data_query(metadata))


def attach_data_resource_metadata(
    surface: type,
    metadata: DataResourceMetadata,
) -> type:
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
        merged[item.model_label] = item if existing is None else _merge_data_resource(existing, item)
    return tuple(merged.values())


def serialize_data_resources(
    metadata: tuple[DataResourceMetadata, ...],
    *,
    schema_name: str,
) -> list[dict[str, object]]:
    """Return a JSON-safe schema-extension payload for resource metadata."""

    return [_serialize_data_resource(item, schema_name=schema_name) for item in metadata]


def serialize_data_queries(
    metadata: tuple[DataQueryMetadata, ...],
) -> list[dict[str, object]]:
    """Return a JSON-safe schema-extension payload for data-query metadata."""

    return [_serialize_data_query(item) for item in metadata]


def _serialize_data_resource(metadata: DataResourceMetadata, *, schema_name: str) -> dict[str, object]:
    """Return one JSON-safe resource metadata mapping."""

    return {
        "schemaName": schema_name,
        "modelLabel": metadata.model_label,
        "appLabel": metadata.app_label,
        "modelName": metadata.model_name,
        "publicIdField": metadata.public_id_field,
        "roots": {
            "list": metadata.roots.list_name,
            "detail": metadata.roots.detail_name,
            "aggregate": metadata.roots.aggregate_name,
            "groups": metadata.roots.group_name,
            "create": metadata.roots.create_name,
            "update": metadata.roots.update_name,
            "delete": metadata.roots.delete_name,
            "deletePreview": metadata.roots.delete_preview_name,
            "revisions": metadata.roots.revisions_name,
            "changes": metadata.roots.changes_name,
        },
        "typeNames": {
            "query": metadata.type_names.query,
            "node": metadata.type_names.node,
            "filter": metadata.type_names.filter,
            "order": metadata.type_names.order,
            "aggregate": metadata.type_names.aggregate,
            "grouped": metadata.type_names.grouped,
            "groupKey": metadata.type_names.group_key,
            "groupBySpec": metadata.type_names.group_by_spec,
            "groupOrder": metadata.type_names.group_order,
            "having": metadata.type_names.having,
            "createInput": metadata.type_names.create_input,
            "updateInput": metadata.type_names.update_input,
            "deletePayload": metadata.type_names.delete_payload,
            "revision": metadata.type_names.revision,
        },
        "capabilities": list(metadata.capabilities),
        "fields": [
            {
                "name": field.name,
                "kind": field.kind,
                "scalar": field.scalar,
                "widget": field.widget,
                "readable": field.readable,
                "filterable": field.filterable,
                "sortable": field.sortable,
                "aggregatable": field.aggregatable,
                "groupable": field.groupable,
                "creatable": field.creatable,
                "updatable": field.updatable,
                "requiredOnCreate": field.required_on_create,
                "relationModelLabel": field.relation_model_label,
                "relationLabelAxis": field.relation_label_axis,
            }
            for field in metadata.fields
        ],
        "filterFields": list(metadata.filter_fields),
        "orderFields": list(metadata.order_fields),
        "aggregateFields": list(metadata.aggregate_fields),
        "groupByFields": list(metadata.group_by_fields),
        "groupDimensions": [
            {
                "field": dimension.field,
                "input": dimension.input,
                "key": dimension.key,
                "kind": dimension.kind,
                "scalar": dimension.scalar,
                "extractions": [
                    {
                        "name": extraction.name,
                        "input": extraction.input,
                        "key": extraction.key,
                        "rangeKey": extraction.range_key,
                    }
                    for extraction in dimension.extractions
                ],
            }
            for dimension in metadata.group_dimensions
        ],
        "aggregateMeasures": [
            {
                "op": measure.op,
                "field": measure.field,
                "input": measure.input,
            }
            for measure in metadata.aggregate_measures
        ],
        "defaultMeasures": [
            {
                "op": measure.op,
                "field": measure.field,
                "input": measure.input,
            }
            for measure in metadata.default_measures
        ],
        "defaultSort": [
            {
                "field": sort.field,
                "direction": sort.direction,
            }
            for sort in metadata.default_sort
        ],
        "createFields": list(metadata.create_fields),
        "updateFields": list(metadata.update_fields),
        "requiredCreateFields": list(metadata.required_create_fields),
        "revisionFields": list(metadata.revision_fields),
        "relationAxes": [
            {
                "field": axis.field,
                "modelLabel": axis.model_label,
                "publicIdField": axis.public_id_field,
                "labelAxis": axis.label_axis,
            }
            for axis in metadata.relation_axes
        ],
        "groupAliases": [
            {
                "field": alias.field,
                "aggregateField": alias.aggregate_field,
                "aggregateKey": alias.aggregate_key,
            }
            for alias in metadata.group_aliases
        ],
    }


def _serialize_data_query(metadata: DataQueryMetadata) -> dict[str, object]:
    """Return one JSON-safe data-query metadata mapping."""

    return {
        "modelLabel": metadata.model_label,
        "appLabel": metadata.app_label,
        "modelName": metadata.model_name,
        "publicIdField": metadata.public_id_field,
        "roots": {
            "listName": _wire_name_or_none(metadata.roots.list_name),
            "detailName": _wire_name_or_none(metadata.roots.detail_name),
            "aggregateName": _wire_name_or_none(metadata.roots.aggregate_name),
            "groupName": _wire_name_or_none(metadata.roots.group_name),
        },
        "typeNames": {
            "query": metadata.type_names.query,
            "node": metadata.type_names.node,
            "filter": metadata.type_names.filter,
            "order": metadata.type_names.order,
            "aggregate": metadata.type_names.aggregate,
            "grouped": metadata.type_names.grouped,
            "groupKey": metadata.type_names.group_key,
            "groupBySpec": metadata.type_names.group_by_spec,
            "groupOrder": metadata.type_names.group_order,
            "having": metadata.type_names.having,
        },
        "capabilities": list(metadata.capabilities),
        "filterFields": [_wire_name(field) for field in metadata.filter_fields],
        "orderFields": [_wire_name(field) for field in metadata.order_fields],
        "aggregateFields": [_wire_name(field) for field in metadata.aggregate_fields],
        "groupByFields": [_wire_name(field) for field in metadata.group_by_fields],
        "groupDimensions": [
            {
                "field": dimension.field,
                "input": dimension.input,
                "key": dimension.key,
                "kind": dimension.kind,
                "scalar": dimension.scalar,
                "extractions": [
                    {
                        "name": extraction.name,
                        "input": extraction.input,
                        "key": extraction.key,
                        "rangeKey": extraction.range_key,
                    }
                    for extraction in dimension.extractions
                ],
            }
            for dimension in metadata.group_dimensions
        ],
        "aggregateMeasures": [
            {
                "op": measure.op,
                "field": measure.field,
                "input": measure.input,
            }
            for measure in metadata.aggregate_measures
        ],
        "defaultMeasures": [
            {
                "op": measure.op,
                "field": measure.field,
                "input": measure.input,
            }
            for measure in metadata.default_measures
        ],
        "defaultSort": [
            {
                "field": sort.field,
                "direction": sort.direction,
            }
            for sort in metadata.default_sort
        ],
        "relationAxes": [
            {
                "field": _wire_name(axis.field),
                "modelLabel": axis.model_label,
                "publicIdField": axis.public_id_field,
                "labelAxis": _wire_name_or_none(axis.label_axis),
            }
            for axis in metadata.relation_axes
        ],
        "groupAliases": [
            {
                "field": _wire_name(alias.field),
                "aggregateField": _wire_name(alias.aggregate_field),
                "aggregateKey": _wire_name(alias.aggregate_key),
            }
            for alias in metadata.group_aliases
        ],
    }


def _merge_data_resource(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
) -> DataResourceMetadata:
    """Return two same-model resource contributions folded into one."""

    if left.model is not right.model:
        raise ImproperlyConfigured(
            f"resource metadata model label '{left.model_label}' is contributed by both "
            f"{left.model._meta.label} and {right.model._meta.label}."
        )
    return DataResourceMetadata(
        model=left.model,
        model_label=left.model_label,
        app_label=left.app_label,
        model_name=left.model_name,
        public_id_field=cast(
            str,
            _merge_value(left, right, "public_id_field", left.public_id_field, right.public_id_field),
        ),
        roots=_merge_roots(left, right),
        type_names=_merge_type_names(left, right),
        capabilities=_merge_capabilities(left.capabilities, right.capabilities),
        fields=_merge_resource_fields(left.fields, right.fields),
        filter_fields=left.filter_fields or right.filter_fields,
        order_fields=left.order_fields or right.order_fields,
        aggregate_fields=left.aggregate_fields or right.aggregate_fields,
        group_by_fields=left.group_by_fields or right.group_by_fields,
        group_dimensions=left.group_dimensions or right.group_dimensions,
        aggregate_measures=left.aggregate_measures or right.aggregate_measures,
        default_measures=left.default_measures or right.default_measures,
        default_sort=left.default_sort or right.default_sort,
        create_fields=left.create_fields or right.create_fields,
        update_fields=left.update_fields or right.update_fields,
        required_create_fields=left.required_create_fields or right.required_create_fields,
        revision_fields=left.revision_fields or right.revision_fields,
        relation_axes=left.relation_axes or right.relation_axes,
        group_aliases=left.group_aliases or right.group_aliases,
        node_type=left.node_type or right.node_type,
        filter_type=left.filter_type or right.filter_type,
        order_type=left.order_type or right.order_type,
    )


def _merge_roots(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
) -> DataResourceRoots:
    """Return merged root names after fail-fast collision checks."""

    return DataResourceRoots(
        **{
            name: _merge_value(left, right, name, getattr(left.roots, name), getattr(right.roots, name))
            for name in _RESOURCE_ROOT_FIELDS
        }
    )


def _merge_type_names(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
) -> DataResourceTypeNames:
    """Return merged type names after fail-fast collision checks."""

    return DataResourceTypeNames(
        **{
            name: _merge_value(left, right, name, getattr(left.type_names, name), getattr(right.type_names, name))
            for name in _RESOURCE_TYPE_FIELDS
        }
    )


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
            f"resource metadata for {left.model_label} has conflicting {name}: "
            f"{left_value!r} and {right_value!r}."
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
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares duplicate {purpose} '{value}'."
            )
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
            f"resource metadata for {model_label} field '{field.name}' declares "
            f"unsupported kind '{field.kind}'."
        )
    if field.scalar is not None and field.scalar not in _RESOURCE_FIELD_SCALARS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares "
            f"unsupported scalar '{field.scalar}'."
        )
    if field.widget is not None and field.widget not in _RESOURCE_FIELD_WIDGETS:
        raise ImproperlyConfigured(
            f"resource metadata for {model_label} field '{field.name}' declares "
            f"unsupported widget '{field.widget}'."
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


def _capabilities(
    roots: DataQueryRoots,
    enable_filter_echo: bool,
) -> tuple[str, ...]:
    """Return stable capability names for roots present on the data query."""

    capabilities: list[str] = []
    if roots.list_name is not None:
        capabilities.append("list")
    if roots.detail_name is not None:
        capabilities.append("detail")
    if roots.aggregate_name is not None:
        capabilities.append("aggregate")
    if roots.group_name is not None:
        capabilities.append("groups")
    if enable_filter_echo and roots.group_name is not None:
        capabilities.append("filterEcho")
    return tuple(capabilities)


def _input_fields(surface: type | None) -> tuple[str, ...]:
    """Return declared input fields, excluding Strawberry-Django filter controls."""

    if surface is None:
        return ()
    return tuple(name for name in surface_field_names(surface) if name not in _FILTER_CONTROL_FIELDS)


def _input_wire_fields(surface: type | None, *, exclude: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Return declared input fields as GraphQL wire names."""

    excluded = set(exclude)
    return tuple(
        _surface_wire_field_name(surface, name) or _wire_name(name)
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
        required.append(str(field.graphql_name or to_camel_case(field.python_name)))
    return tuple(required)


def _resource_fields(
    node_type: type,
    model: type[models.Model],
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
        kind = _field_kind(
            model_field,
            axis,
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
        fields.append(
            DataResourceFieldMetadata(
                name=name,
                kind=kind,
                scalar=scalar,
                widget=None if scalar == "ID" else _field_widget(model_field, kind),
                filterable=name in filterable,
                sortable=name in sortable,
                aggregatable=name in aggregatable,
                groupable=name in groupable,
                creatable=name in creatable,
                updatable=name in updatable,
                required_on_create=name in required_on_create,
                relation_model_label=axis.model_label if axis is not None else None,
                relation_label_axis=axis.label_axis if axis is not None else None,
            )
        )
    return tuple(fields)


def _surface_wire_field_names(surface: type | None, names: tuple[str, ...]) -> tuple[str, ...]:
    """Return GraphQL wire names for ``names`` on ``surface`` when possible."""

    if surface is None:
        return _wire_paths(names)
    return tuple(_surface_wire_field_name(surface, name) or _wire_name(name) for name in names)


def _surface_wire_field_name(surface: type | None, name: str | None) -> str | None:
    """Return the actual GraphQL wire field name for a Strawberry field."""

    if surface is None or name is None:
        return None
    definition = get_object_definition(surface)
    if definition is not None:
        for field in definition.fields:
            if field.python_name == name:
                return str(field.graphql_name or to_camel_case(field.python_name))
    return _wire_name(name)


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


def _wire_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    """Return legacy GraphQL wire names for model field paths."""

    return tuple(_wire_name(path) for path in paths)


def _wire_relation_axes(
    axes: tuple[DataRelationAxisMetadata, ...],
) -> tuple[DataRelationAxisMetadata, ...]:
    """Return relation axis metadata in GraphQL wire names."""

    return tuple(
        DataRelationAxisMetadata(
            field=_wire_name(axis.field),
            model_label=axis.model_label,
            public_id_field=axis.public_id_field,
            label_axis=_wire_name_or_none(axis.label_axis),
        )
        for axis in axes
    )


def _wire_group_aliases(
    aliases: tuple[DataGroupAliasMetadata, ...],
) -> tuple[DataGroupAliasMetadata, ...]:
    """Return group alias metadata in GraphQL wire names."""

    return tuple(
        DataGroupAliasMetadata(
            field=_wire_name(alias.field),
            aggregate_field=_wire_name(alias.aggregate_field),
            aggregate_key=_wire_name(alias.aggregate_key),
        )
        for alias in aliases
    )


def _wire_group_dimensions(
    dimensions: tuple[DataGroupDimensionMetadata, ...],
) -> tuple[DataGroupDimensionMetadata, ...]:
    """Return group dimensions in GraphQL wire names."""

    return tuple(
        DataGroupDimensionMetadata(
            field=_wire_name(dimension.field),
            input=dimension.input,
            key=_wire_name(dimension.key),
            kind=dimension.kind,
            scalar=dimension.scalar,
            extractions=tuple(
                DataGroupExtractionMetadata(
                    name=extraction.name,
                    input=extraction.input,
                    key=_wire_name(extraction.key),
                    range_key=_wire_name_or_none(extraction.range_key),
                )
                for extraction in dimension.extractions
            ),
        )
        for dimension in dimensions
    )


def _wire_aggregate_measures(
    measures: tuple[DataAggregateMeasureMetadata, ...],
) -> tuple[DataAggregateMeasureMetadata, ...]:
    """Return aggregate measure metadata in GraphQL wire names."""

    return tuple(
        DataAggregateMeasureMetadata(
            op=measure.op,
            field=_wire_name_or_none(measure.field),
            input=measure.input,
        )
        for measure in measures
    )


def _wire_default_sort(
    sorts: tuple[DataDefaultSortMetadata, ...],
) -> tuple[DataDefaultSortMetadata, ...]:
    """Return default sort metadata in GraphQL wire names."""

    return tuple(
        DataDefaultSortMetadata(
            field=_wire_name(sort.field),
            direction=sort.direction,
        )
        for sort in sorts
    )


def _wire_name_or_none(value: str | None) -> str | None:
    """Return a GraphQL wire field name for ``value`` when present."""

    return None if value is None else _wire_name(value)


def _wire_name(value: str) -> str:
    """Return Strawberry's GraphQL wire field name for a Python field name."""

    return to_camel_case(value)


def _model_field_or_none(model: type[models.Model], name: str) -> models.Field[Any, Any] | None:
    """Return a Django model field for ``name`` when one owns that GraphQL field."""

    try:
        return model._meta.get_field(name)
    except FieldDoesNotExist:
        return None


def _field_kind(
    field: models.Field[Any, Any] | None,
    relation_axis: DataRelationAxisMetadata | None,
    *,
    is_list: bool = False,
    is_enum: bool = False,
    is_object: bool = False,
) -> str:
    """Return a coarse field kind for resource metadata."""

    if is_list or (field is not None and _is_to_many_relation(field)):
        return "list"
    if is_object or relation_axis is not None or (field is not None and field.is_relation):
        return "relation"
    if is_enum or (field is not None and getattr(field, "choices", None)):
        return "enum"
    if field is not None and getattr(field, "many_to_many", False):
        return "list"
    return "scalar"


def _field_scalar(field: models.Field[Any, Any] | None, kind: str) -> str | None:
    """Return the scalar family represented by ``field`` when known."""

    if kind in {"relation", "enum"}:
        return None
    if kind == "list" and (field is None or field.is_relation):
        return None
    if field is None:
        return None
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
    if isinstance(field, (models.CharField, models.TextField)):
        return "String"
    raise ImproperlyConfigured(
        f"resource metadata for {field.model._meta.label} cannot classify unsupported "
        f"field '{field.name}' ({field.__class__.__name__})."
    )


def _field_widget(field: models.Field[Any, Any] | None, kind: str) -> str | None:
    """Return the default rendered widget owned by the model field shape."""

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


def _group_dimensions(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[DataGroupDimensionMetadata, ...]:
    """Return backend-owned grouped bucket dimensions."""

    return tuple(_group_dimension(model, path) for path in group_by_fields)


def _group_dimension(
    model: type[models.Model],
    path: str,
) -> DataGroupDimensionMetadata:
    """Return one group dimension declaration."""

    field = _require_model_field_for_path(model, path, purpose="group axis")
    key = _group_key_path(field, path)
    kind = "relation" if field is not None and _is_to_one_relation(field) else "column"
    scalar = "ID" if kind == "relation" else _field_scalar(field, _field_kind(field, None))
    return DataGroupDimensionMetadata(
        field=path,
        input=_group_input_name(path),
        key=key,
        kind=kind,
        scalar=scalar,
        extractions=_group_extractions(field, key),
    )


def _group_input_name(path: str) -> str:
    """Return the generated groupable enum value for one model field path."""

    return path.replace(".", "__").upper()


def _group_key_path(
    field: models.Field[Any, Any] | None,
    path: str,
) -> str:
    """Return the grouped result key field path for one grouping axis."""

    if "__" not in path and field is not None and _is_to_one_relation(field):
        return f"{path}_id"
    return path.replace(".", "__")


def _group_extractions(
    field: models.Field[Any, Any] | None,
    key: str,
) -> tuple[DataGroupExtractionMetadata, ...]:
    """Return supported extraction functions for a group axis."""

    if not isinstance(field, (models.DateField, models.DateTimeField)):
        return ()
    return tuple(
        DataGroupExtractionMetadata(
            name=name,
            input=input_name,
            key=f"{key}_{key_suffix}",
            range_key=f"{key}_{range_suffix}" if range_suffix is not None else None,
        )
        for name, input_name, key_suffix, range_suffix in _GROUP_EXTRACTIONS
    )


def _aggregate_measures(
    model: type[models.Model],
    aggregate_fields: tuple[str, ...],
) -> tuple[DataAggregateMeasureMetadata, ...]:
    """Return aggregate measures consumed by the frontend measure extractors."""

    measures: list[DataAggregateMeasureMetadata] = []
    for path in aggregate_fields:
        field = _require_model_field_for_path(model, path, purpose="aggregate measure")
        if getattr(field, "primary_key", False):
            continue
        ops = _measure_ops_for_field(field)
        if not ops:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) declares unsupported aggregate measure "
                f"field path '{path}' ({field.__class__.__name__})."
            )
        measures.extend(
            DataAggregateMeasureMetadata(op=op, field=path, input=_group_input_name(path))
            for op in ops
        )
    return tuple(measures)


def _measure_ops_for_field(field: models.Field[Any, Any]) -> tuple[str, ...]:
    """Return frontend-supported aggregate operators for one Django field."""

    if isinstance(field, (models.IntegerField, models.DecimalField, models.FloatField)):
        return _FRONTEND_MEASURE_OPS
    if isinstance(field, (models.DateField, models.DateTimeField)):
        return ("min", "max")
    return ()


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
                f"data_query({model._meta.label}) cannot expose non-string default ordering {term!r}."
            )
        if term == "?":
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) cannot expose random default ordering."
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
        if not _is_to_one_relation(field):
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


def _group_aliases(
    node_type: type,
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
    group_aliases: tuple[tuple[str, str], ...],
) -> tuple[DataGroupAliasMetadata, ...]:
    """Return validated group aliases declared by a data-query surface."""

    node_fields = set(surface_field_names(node_type))
    aliases: list[DataGroupAliasMetadata] = []
    seen: set[str] = set()
    for field, aggregate_field in group_aliases:
        if field in seen:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) declares duplicate group alias '{field}'."
            )
        seen.add(field)
        if field not in node_fields:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) group alias '{field}' is not a field on "
                f"{surface_name(node_type)}."
            )
        if aggregate_field not in group_by_fields:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) group alias '{field}' targets "
                f"non-groupable aggregate axis '{aggregate_field}'."
            )
        aliases.append(
            DataGroupAliasMetadata(
                field=field,
                aggregate_field=aggregate_field,
                aggregate_key=aggregate_field,
            )
        )
    return tuple(aliases)


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
        if not _is_to_one_relation(field):
            continue
        if relation not in direct_axes:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) relation label axis '{path}' "
                f"requires matching direct relation group axis '{relation}'."
            )
        existing = label_axes.get(relation)
        if existing is not None and existing != path:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) relation group axis '{relation}' "
                f"declares multiple label axes: '{existing}' and '{path}'."
            )
        label_axes[relation] = path
    return label_axes


def _is_to_one_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether ``field`` is a forward to-one relation."""

    return bool(getattr(field, "many_to_one", False) or getattr(field, "one_to_one", False))


def _is_to_many_relation(field: models.Field[Any, Any]) -> bool:
    """Return whether ``field`` represents a to-many relation path."""

    return bool(getattr(field, "many_to_many", False) or getattr(field, "one_to_many", False))


def _require_model_field_for_path(
    model: type[models.Model],
    path: str,
    *,
    purpose: str,
) -> models.Field[Any, Any]:
    """Return a concrete model field for ``path`` or fail at metadata emission."""

    current_model: type[models.Model] | None = model
    field: models.Field[Any, Any] | None = None
    for part in path.replace(".", "__").split("__"):
        if current_model is None:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) declares unknown {purpose} field path '{path}'."
            )
        try:
            field = current_model._meta.get_field(part)
        except FieldDoesNotExist:
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) declares unknown {purpose} field path '{path}'."
            ) from None
        if _is_to_many_relation(field):
            raise ImproperlyConfigured(
                f"data_query({model._meta.label}) declares unsupported to-many {purpose} field path '{path}'."
            )
        remote_field = getattr(field, "remote_field", None)
        related_model = getattr(remote_field, "model", None)
        current_model = related_model if isinstance(related_model, type) else None
    if field is None:
        raise ImproperlyConfigured(
            f"data_query({model._meta.label}) declares unknown {purpose} field path '{path}'."
        )
    return field


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
    model: type[models.Model],
) -> tuple[str, str]:
    """Return metadata app/model names for a public model label."""

    if model_label == model._meta.label:
        return model._meta.app_label, model._meta.model_name
    app_label, object_name = model_label.split(".", 1)
    return app_label, object_name.lower()
