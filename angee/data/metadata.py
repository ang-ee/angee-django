"""Transport-neutral data-surface description values and operations.

This module owns the frozen description objects, their merge rules, and their
JSON-safe envelope serialization. Projection layers supply opaque model/surface
handles and choose projection defaults; the contract only stores and describes
those facts.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, cast

from django.core.exceptions import ImproperlyConfigured
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
    "merge_data_resources",
    "merge_resource_fields",
    "serialize_data_resources",
]

_RESOURCE_CAPABILITY_ORDER = (
    "list",
    "detail",
    "aggregate",
    "groups",
    "filterEcho",
    "revisions",
    "create",
    "update",
    "save",
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
class DataResourceSubtitleMetadata:
    """Declared dotted selection paths for a resource record's subtitle facts.

    The closed ``created``/``updated``/``word_count`` fact set is the renderer's
    vocabulary; adding a fact extends this declaration and its presentation
    together at the same seam.
    """

    created: str | None = None
    updated: str | None = None
    word_count: str | None = None

    def merge(
        self,
        left: DataResourceMetadata,
        right: DataResourceMetadata,
    ) -> DataResourceSubtitleMetadata:
        """Merge facts using the resource metadata's singleton collision rule."""

        right_subtitle = cast(DataResourceSubtitleMetadata, right.subtitle)
        return DataResourceSubtitleMetadata(
            **{
                field_def.name: _merge_value(
                    left,
                    right,
                    f"subtitle.{field_def.name}",
                    getattr(self, field_def.name),
                    getattr(right_subtitle, field_def.name),
                )
                for field_def in dataclasses.fields(DataResourceSubtitleMetadata)
            }
        )


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
            contributors=_merge_contributors(self.contributors, other.contributors),
            canonical_label=cast(
                str | None,
                _merge_value(
                    self,
                    other,
                    "canonical_label",
                    self.canonical_label,
                    other.canonical_label,
                ),
            ),
            row_model=_merge_row_model(self, other),
            record_representation=cast(
                str | None,
                _merge_value(
                    self,
                    other,
                    "record_representation",
                    self.record_representation,
                    other.record_representation,
                ),
            ),
            subtitle=_merge_subtitle(self, other),
            impl_fields=self.impl_fields or other.impl_fields,
            capabilities=_merge_capabilities(self.capabilities, other.capabilities),
            fields=merge_resource_fields(self.fields, other.fields),
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
            lines=self.lines or other.lines,
            node_type=self.node_type or other.node_type,
            filter_type=self.filter_type or other.filter_type,
            order_type=self.order_type or other.order_type,
        )

    def as_wire(self, *, schema_name: str) -> dict[str, object]:
        """Return this resource metadata in JSON-safe frontend wire shape."""

        return {"schemaName": schema_name, **_wire_dataclass(self)}


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
            archivable=existing.archivable or field.archivable,
            currency_field=existing.currency_field or field.currency_field,
            relation_model_label=existing.relation_model_label or field.relation_model_label,
            relation_label_axis=existing.relation_label_axis or field.relation_label_axis,
            relation_object=existing.relation_object or field.relation_object,
        )
    return tuple(by_name[name] for name in order)


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
            f"{left.row_model!r} from {_contributor_names(left)} and "
            f"{right.row_model!r} from {_contributor_names(right)}."
        )
    return left.row_model


def _merge_subtitle(
    left: DataResourceMetadata,
    right: DataResourceMetadata,
) -> DataResourceSubtitleMetadata | None:
    """Return subtitle facts merged with the existing singleton collision rule."""

    if left.subtitle is None:
        return right.subtitle
    if right.subtitle is None:
        return left.subtitle
    return left.subtitle.merge(left, right)


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
            f"{left_value!r} from {_contributor_names(left)} and "
            f"{right_value!r} from {_contributor_names(right)}."
        )
    return left_value if left_value is not None else right_value


def _merge_contributors(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Return contributor names in first-seen order for later diagnostics."""

    return tuple(dict.fromkeys((*left, *right)))


def _contributor_names(metadata: DataResourceMetadata) -> str:
    """Return the surfaces/types that donated one metadata contribution."""

    return ", ".join(metadata.contributors) or metadata.model_label


def _merge_capabilities(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[str, ...]:
    """Return deterministic capability names from both resource contributions."""

    names = {*left, *right}
    ordered = [name for name in _RESOURCE_CAPABILITY_ORDER if name in names]
    ordered.extend(sorted(names - set(_RESOURCE_CAPABILITY_ORDER)))
    return tuple(ordered)


def _metadata_key(name: str) -> str:
    """Return the contract's camelCase JSON key for one metadata field.

    Envelope keys are camelCase independently of the GraphQL wire field names,
    which remain snake_case. This intentionally matches Strawberry's
    ``to_camel_case`` algorithm without importing Strawberry, keeping historical
    envelopes byte-stable while the contract remains outside that dependency.
    """

    first, *rest = name.split("_")
    return first + "".join(part.capitalize() if part else "_" for part in rest)
