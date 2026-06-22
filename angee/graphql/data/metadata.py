"""Metadata for Angee model-backed data query surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models
from strawberry.types import get_object_definition
from strawberry.utils.str_converters import to_camel_case

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.introspection import surface_field_names, surface_name

DATA_QUERY_METADATA_ATTR = "__angee_data_query__"
"""Attribute attached to generated data-query root classes."""

_FILTER_CONTROL_FIELDS = frozenset({"AND", "OR", "NOT", "DISTINCT", "and", "or", "not", "distinct"})


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
    grouped_result: str | None = None
    group_key: str | None = None
    group_by_spec: str | None = None
    groupable_field_enum: str | None = None
    having: str | None = None


@dataclass(frozen=True, slots=True)
class DataRelationAxisMetadata:
    """Metadata for a relation group axis and its public identity lookup."""

    field: str
    model_label: str
    public_id_field: str
    label_axis: str | None = None


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
    relation_axes: tuple[DataRelationAxisMetadata, ...]
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
    enable_filter_echo: bool,
    aggregate_type: type | None = None,
    grouped_type: type | None = None,
    grouped_result_type: type | None = None,
    group_key_type: type | None = None,
    group_by_spec_type: type | None = None,
    groupable_field_enum: type | None = None,
    having_type: type | None = None,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
) -> DataQueryMetadata:
    """Build metadata for a generated data-query class."""

    exposed_model_label = model_label or model._meta.label
    app_label, model_name = _model_label_parts(exposed_model_label, model)
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
            grouped_result=_optional_type_name(grouped_result_type),
            group_key=_optional_type_name(group_key_type),
            group_by_spec=_optional_type_name(group_by_spec_type),
            groupable_field_enum=_optional_type_name(groupable_field_enum),
            having=_optional_type_name(having_type),
        ),
        capabilities=_capabilities(roots, enable_filter_echo),
        filter_fields=_input_fields(filter_type),
        order_fields=_input_fields(order_type),
        aggregate_fields=aggregate_fields,
        group_by_fields=group_by_fields,
        relation_axes=_relation_axes(model, group_by_fields),
        filter_type=filter_type,
        order_type=order_type,
    )


def attach_data_query_metadata(
    query_type: type,
    metadata: DataQueryMetadata,
) -> type:
    """Attach data-query metadata to a generated Strawberry query class."""

    setattr(query_type, DATA_QUERY_METADATA_ATTR, metadata)
    return query_type


def serialize_data_queries(
    metadata: tuple[DataQueryMetadata, ...],
) -> list[dict[str, object]]:
    """Return a JSON-safe schema-extension payload for data-query metadata."""

    return [_serialize_data_query(item) for item in metadata]


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
            "groupedResult": metadata.type_names.grouped_result,
            "groupKey": metadata.type_names.group_key,
            "groupBySpec": metadata.type_names.group_by_spec,
            "groupableFieldEnum": metadata.type_names.groupable_field_enum,
            "having": metadata.type_names.having,
        },
        "capabilities": list(metadata.capabilities),
        "filterFields": [_wire_name(field) for field in metadata.filter_fields],
        "orderFields": [_wire_name(field) for field in metadata.order_fields],
        "aggregateFields": [_wire_name(field) for field in metadata.aggregate_fields],
        "groupByFields": [_wire_name(field) for field in metadata.group_by_fields],
        "relationAxes": [
            {
                "field": _wire_name(axis.field),
                "modelLabel": axis.model_label,
                "publicIdField": axis.public_id_field,
                "labelAxis": _wire_name_or_none(axis.label_axis),
            }
            for axis in metadata.relation_axes
        ],
    }


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


def _wire_name_or_none(value: str | None) -> str | None:
    """Return a GraphQL wire field name for ``value`` when present."""

    return None if value is None else _wire_name(value)


def _wire_name(value: str) -> str:
    """Return Strawberry's GraphQL wire field name for a Python field name."""

    return to_camel_case(value)


def _relation_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[DataRelationAxisMetadata, ...]:
    """Return direct FK group axes with their related model and optional label axis."""

    label_axes = {path.split("__", 1)[0]: path for path in group_by_fields if "__" in path}
    relation_axes: list[DataRelationAxisMetadata] = []
    for path in group_by_fields:
        if "__" in path:
            continue
        field = model._meta.get_field(path)
        if not getattr(field, "many_to_one", False):
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
