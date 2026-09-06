"""Transport-neutral data-surface description values and operations.

This module owns the frozen description objects and their JSON-safe envelope
serialization. Projection layers supply the final facts; the contract only stores
and describes them.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from django.db import models

__all__ = [
    "DataAggregateMeasureMetadata",
    "DataDefaultSortMetadata",
    "DataGroupAliasMetadata",
    "DataGroupBucketFilterMetadata",
    "DataGroupBucketFilterValueMapMetadata",
    "DataGroupDimensionMetadata",
    "DataGroupExtractionMetadata",
    "DataLinesMetadata",
    "DataRelationAxisMetadata",
    "DataResourceEnumValueMetadata",
    "DataResourceFieldMetadata",
    "DataResourceMetadata",
    "DataResourceRoots",
    "DataResourceSubtitleMetadata",
    "DataResourceTypeNames",
    "serialize_data_resources",
]

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
    archivable: bool = False
    currency_field: str | None = None
    relation_model_label: str | None = None
    relation_label_axis: str | None = None
    relation_object: bool = False
    """Whether a ``relation`` field is projected as a nested selectable object.

    A to-one FK can surface two ways with identical ``relation`` semantics
    (``many2one`` widget, relation filter/group axis): as a nested node
    (``product: ProductVariantType`` — its subfields are selectable) or as the
    related row's public id (``location: strawberry.ID`` — a leaf). Only the
    former may be read with a sub-selection; the frontend keys the row selection on
    this flag so a nested relation reads ``{ id <label> }`` and an id projection
    stays a leaf.
    """
    model_field_name: str | None = dataclasses.field(default=None, metadata={"wire": False})
    """Owning Django field name when the final GraphQL field is aliased."""


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
    null_lookup: str | None = "isNull"
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
class DataLinesMetadata:
    """Editable child-lines contract for one document resource.

    Emitted when a resource declares ``lines=`` (F6): the frontend reads it to
    drive the ``EditableLines`` composer and the authored ``<res>_save``
    diff-apply mutation. ``field`` is the parent's child accessor, ``model_label``
    the child model, ``input_type`` the shared GraphQL line input (an optional
    public ``id`` plus the editable child columns), and ``fields`` the per-column
    metadata (scalar/widget) the line cells render. ``position_field`` names the
    integer order column when the child carries one.
    """

    field: str
    model_label: str
    input_type: str | None = None
    fields: tuple[DataResourceFieldMetadata, ...] = ()
    position_field: str | None = None


@dataclass(frozen=True, slots=True)
class DataResourceRoots:
    """GraphQL wire root names emitted for one model data resource."""

    list_name: str | None = dataclasses.field(default=None, metadata={"wire": "list"})
    detail_name: str | None = dataclasses.field(default=None, metadata={"wire": "detail"})
    aggregate_name: str | None = dataclasses.field(default=None, metadata={"wire": "aggregate"})
    group_name: str | None = dataclasses.field(default=None, metadata={"wire": "groups"})
    group_count_name: str | None = dataclasses.field(default=None, metadata={"wire": "groupsCount"})
    create_name: str | None = dataclasses.field(default=None, metadata={"wire": "create"})
    update_name: str | None = dataclasses.field(default=None, metadata={"wire": "update"})
    save_name: str | None = dataclasses.field(default=None, metadata={"wire": "save"})
    delete_name: str | None = dataclasses.field(default=None, metadata={"wire": "delete"})
    delete_preview_name: str | None = dataclasses.field(default=None, metadata={"wire": "deletePreview"})
    revisions_name: str | None = dataclasses.field(default=None, metadata={"wire": "revisions"})
    changes_name: str | None = dataclasses.field(default=None, metadata={"wire": "changes"})

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
class DataResourceSubtitleMetadata:
    """Declared dotted selection paths for a resource record's subtitle facts.

    The closed ``created``/``updated``/``word_count`` fact set is the renderer's
    vocabulary; adding a fact extends this declaration and its presentation
    together at the same seam.
    """

    created: str | None = None
    updated: str | None = None
    word_count: str | None = None

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
    contributors: tuple[str, ...] = dataclasses.field(
        default=(),
        compare=False,
        repr=False,
        metadata={"wire": False},
    )
    canonical_label: str | None = None
    row_model: str = "server"
    record_representation: str | None = None
    subtitle: DataResourceSubtitleMetadata | None = None
    impl_fields: tuple[str, ...] = ()
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
    lines: DataLinesMetadata | None = dataclasses.field(default=None, metadata={"wire": "linesResource"})

    def as_wire(self, *, schema_name: str) -> dict[str, object]:
        """Return this resource metadata in JSON-safe frontend wire shape."""

        return {"schemaName": schema_name, **_wire_dataclass(self)}


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


def _metadata_key(name: str) -> str:
    """Return the contract's camelCase JSON key for one metadata field.

    Envelope keys are camelCase independently of the GraphQL wire field names,
    which remain snake_case. This intentionally matches Strawberry's
    ``to_camel_case`` algorithm without importing Strawberry, keeping historical
    envelopes byte-stable while the contract remains outside that dependency.
    """

    first, *rest = name.split("_")
    return first + "".join(part.capitalize() if part else "_" for part in rest)
