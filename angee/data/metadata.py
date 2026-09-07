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
    "DataLinesMetadata",
    "DataQueryAxis",
    "DataQueryDrill",
    "DataQueryExtraction",
    "DataQueryField",
    "DataQueryFilter",
    "DataQueryIdentity",
    "DataQueryOrder",
    "DataQueryRelation",
    "DataQueryServerAxis",
    "DataQuerySort",
    "DataQueryValueMap",
    "DataResourceEnumValueMetadata",
    "DataResourceFieldMetadata",
    "DataResourceMetadata",
    "DataResourceQuery",
    "DataResourceRoots",
    "DataResourceSubtitleMetadata",
    "DataResourceTypeNames",
    "serialize_data_resources",
]


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
    aggregatable: bool = False
    creatable: bool = False
    updatable: bool = False
    required_on_create: bool = False
    archivable: bool = False
    currency_field: str | None = None
    relation_model_label: str | None = None
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
class DataQueryValueMap:
    """One backend-owned enum bucket rewrite into an accepted filter value."""

    from_value: Any = dataclasses.field(metadata={"wire": "from"})
    to_value: Any = dataclasses.field(metadata={"wire": "to"})


@dataclass(frozen=True, slots=True)
class DataQueryDrill:
    """An executable bucket predicate; absence means a summary-only axis.

    JSON path nulls are values, whereas SQL null buckets use ``isNull``. A
    range is half-open and its boundaries come from the aggregate owner.
    """

    kind: str
    field: str
    value_key: str
    range_key: str | None = None
    json_path: str | None = None
    value_transform: str | None = None
    value_map: tuple[DataQueryValueMap, ...] = ()
    null_mode: str = "isNull"


@dataclass(frozen=True, slots=True)
class DataQueryExtraction:
    """One aggregate-owned date extraction and its optional drill capability."""

    name: str
    input: str
    key: str
    range_key: str | None = None
    drill: DataQueryDrill | None = None


@dataclass(frozen=True, slots=True)
class DataQueryServerAxis:
    """Aggregate input and result names, independent of row selection paths."""

    input: str
    key: str
    label_input: str | None = None
    label_key: str | None = None


@dataclass(frozen=True, slots=True)
class DataQueryAxis:
    """One group identity shared by server and client row models.

    Row paths are final selectable fields, absent for aggregate-only axes.
    ``server`` is absent for bounded client row-model axes.
    """

    field: str
    kind: str = "column"
    identity_path: str | None = None
    label_path: str | None = None
    paths: tuple[str, ...] = ()
    server: DataQueryServerAxis | None = None
    extractions: tuple[DataQueryExtraction, ...] = ()
    drill: DataQueryDrill | None = None


@dataclass(frozen=True, slots=True)
class DataQueryIdentity:
    """The final selection field carrying public row identity."""

    field: str


@dataclass(frozen=True, slots=True)
class DataQueryFilter:
    """A final bool-exp field and its executable canonical operators."""

    field: str
    operators: tuple[str, ...]
    scalar: str
    values: tuple[DataResourceEnumValueMetadata, ...] = ()
    value_map: tuple[DataQueryValueMap, ...] = ()


@dataclass(frozen=True, slots=True)
class DataQueryOrder:
    """A final order input field."""

    field: str


@dataclass(frozen=True, slots=True)
class DataQueryRelation:
    """Public relation identity and label selections on this resource."""

    model: str
    identity_path: str | None
    label_path: str | None = None


@dataclass(frozen=True, slots=True)
class DataQueryRow:
    """A semantic row accessor and the final GraphQL paths needed to read it."""

    path: str
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataQueryField:
    """A final readable or queryable field and its executable capabilities."""

    kind: str
    scalar: str | None = None
    values: tuple[DataResourceEnumValueMetadata, ...] = ()
    nullable: bool = True
    filter: DataQueryFilter | None = None
    sort: DataQueryOrder | None = None
    relation: DataQueryRelation | None = None
    row: DataQueryRow | None = None


@dataclass(frozen=True, slots=True)
class DataQuerySort:
    """The resource's declared default ordering."""

    default: tuple[DataDefaultSortMetadata, ...] = ()


@dataclass(frozen=True, slots=True)
class DataResourceQuery:
    """Complete query vocabulary finalized once from the exposed schema.

    Consumers validate intent and project native transport and row-model state.
    Identity, capabilities and group predicates have no parallel wire owners.
    """

    identity: DataQueryIdentity
    fields: dict[str, DataQueryField] = dataclasses.field(default_factory=dict)
    axes: dict[str, DataQueryAxis] = dataclasses.field(default_factory=dict)
    sort: DataQuerySort = dataclasses.field(default_factory=DataQuerySort)
    paging: dict[str, int] = dataclasses.field(default_factory=dict)


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
    query: DataResourceQuery
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
    aggregate_fields: tuple[str, ...] = ()
    aggregate_measures: tuple[DataAggregateMeasureMetadata, ...] = ()
    default_measures: tuple[DataAggregateMeasureMetadata, ...] = ()
    create_fields: tuple[str, ...] = ()
    update_fields: tuple[str, ...] = ()
    required_create_fields: tuple[str, ...] = ()
    revision_fields: tuple[str, ...] = ()
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
    if isinstance(value, dict):
        return {key: _wire_value(item) for key, item in value.items()}
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
