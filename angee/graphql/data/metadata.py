"""Metadata for Angee model-backed data resource surfaces."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, TypeVar, cast

from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac.resources import model_resource_type
from strawberry.utils.str_converters import to_camel_case

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.resource_fields import (
    DataRelationAxisMetadata,
    DataResourceFieldMetadata,
    input_wire_fields,
    merge_resource_fields,
    model_resource_fields,
    require_unique_resource_fields,
    required_input_wire_fields,
    resource_fields,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.introspection import (
    FieldPathError,
    is_to_one_relation,
    require_field_for_path,
)

__all__ = [
    "DATA_RESOURCE_METADATA_ATTR",
    "DataAggregateMeasureMetadata",
    "DataDefaultSortMetadata",
    "DataGroupAliasMetadata",
    "DataGroupBucketFilterMetadata",
    "DataGroupBucketFilterValueMapMetadata",
    "DataGroupDimensionMetadata",
    "DataGroupExtractionMetadata",
    "DataLinesMetadata",
    "DataRelationAxisMetadata",
    "DataResourceFieldMetadata",
    "DataResourceMetadata",
    "DataResourceRoots",
    "DataResourceTypeNames",
    "attach_data_resource_metadata",
    "data_resource_metadata",
    "make_data_resource_metadata",
    "merge_data_resources",
    "model_resource_fields",
    "resource_fields",
    "resource_type_name",
    "resource_wire_field_name",
    "resource_wire_field_names",
    "serialize_data_resources",
]

DATA_RESOURCE_METADATA_ATTR = "__angee_data_resource__"
"""Attribute attached to schema surfaces that contribute model resource metadata."""

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
    record_representation: str | None = None
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
    lines: DataLinesMetadata | None = None,
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
        create_fields or input_wire_fields(create_input_type, exclude=("id",)),
    )
    active_update_fields = _require_unique(
        exposed_model_label,
        "update field",
        update_fields or input_wire_fields(update_input_type, exclude=("id",)),
    )
    active_required_create_fields = _require_unique(
        exposed_model_label,
        "required create field",
        required_create_fields or required_input_wire_fields(create_input_type),
    )
    revision_fields = _require_unique(exposed_model_label, "revision field", revision_fields)
    declared_fields = require_unique_resource_fields(exposed_model_label, fields)
    generated_fields = (
        resource_fields(
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
        merge_resource_fields(generated_fields, declared_fields)
        if declared_fields
        else generated_fields
    )
    active_fields = require_unique_resource_fields(exposed_model_label, active_fields)
    record_representation = _record_representation_field(active_fields)
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
        record_representation=record_representation,
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
        lines=lines,
        node_type=node_type,
        filter_type=filter_type,
        order_type=order_type,
    )


_SurfaceT = TypeVar("_SurfaceT")


def attach_data_resource_metadata(
    surface: type[_SurfaceT],
    metadata: DataResourceMetadata,
) -> type[_SurfaceT]:
    """Attach model resource metadata to a generated Strawberry surface.

    Only query/mutation/subscription *roots* are scanned for resource
    metadata, so an addon that extends another model's GraphQL *type* (a
    ``type_extensions`` entry adds fields to the node, never to the model's
    resource projection) anchors its contribution on one of its own root
    surfaces — typically its action-mutation bucket — and the per-model merge
    (:func:`merge_data_resources`) folds it into the owning model's resource
    by model label. Fields only a server verb advances are contributed
    read-only (neither creatable nor updatable).
    """

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


def _record_representation_field(fields: tuple[DataResourceFieldMetadata, ...]) -> str | None:
    """Return the backend-owned display field for a resource record."""

    candidates = (
        "title",
        "name",
        "displayName",
        "display_name",
        "fullName",
        "full_name",
        "label",
        "username",
        "email",
        "slug",
    )
    by_name = {field.name: field for field in fields}
    for candidate in candidates:
        if _is_display_scalar(by_name.get(candidate)):
            return candidate
    for field in fields:
        if _is_display_scalar(field):
            return field.name
    return None


def _is_display_scalar(field: DataResourceFieldMetadata | None) -> bool:
    """Return whether ``field`` is suitable as a compact record label."""

    return field is not None and field.kind == "scalar" and field.scalar == "String"
def _metadata_key(name: str) -> str:
    """Return the camelCase JSON key for one metadata-envelope dataclass field.

    The frontend ``DataResourceMetadata`` contract keys its objects in camelCase
    (``revisionFields``, ``typeNames``); that is the metadata envelope's own
    naming, distinct from the snake_case GraphQL wire field names above.
    """

    return to_camel_case(name)


def _default_sort(
    model: type[models.Model],
    order_fields: tuple[str, ...],
) -> tuple[DataDefaultSortMetadata, ...]:
    """Return model default ordering terms exposed by the order input."""

    orderable = set(order_fields)
    sorts: list[DataDefaultSortMetadata] = []
    for term in model._meta.ordering:
        if isinstance(term, models.expressions.OrderBy):
            # An expression ordering (F(...).desc(nulls_last=True), say) carries a
            # DB detail — NULL placement — the metadata does not need; expose the
            # axis name and direction it wraps.
            expression = term.expression
            name = getattr(expression, "name", None)
            if not isinstance(name, str):
                raise ImproperlyConfigured(
                    f"resource metadata for {model._meta.label} cannot expose computed default ordering {term!r}."
                )
            term = f"-{name}" if term.descending else name
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
