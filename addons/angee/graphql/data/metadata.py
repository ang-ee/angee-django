"""GraphQL projection and attachment of neutral data-surface descriptions."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, TypeVar

from angee.base.impl import ImplClassField
from angee.base.refs import canonical_record_model
from angee.data import metadata as data_contract
from angee.data.field_classification import is_to_one_relation
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import models
from rebac.resources import model_resource_type

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME
from angee.graphql.data.resource_fields import (
    input_wire_fields,
    model_resource_fields,
    require_resource_selection_path,
    require_unique_resource_fields,
    required_input_wire_fields,
    resource_fields,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.introspection import (
    FieldPathError,
    require_field_for_path,
)

__all__ = [
    "DATA_RESOURCE_METADATA_ATTR",
    "attach_data_resource_metadata",
    "data_resource_metadata",
    "make_data_resource_metadata",
    "model_resource_fields",
    "readable_model_field_names",
    "resource_fields",
    "resource_type_name",
    "resource_wire_field_name",
    "resource_wire_field_names",
]

DATA_RESOURCE_METADATA_ATTR = "__angee_data_resource__"
"""Attribute attached to schema surfaces that contribute model resource metadata."""


def data_resource_metadata(surface: object) -> tuple[data_contract.DataResourceMetadata, ...]:
    """Return model resource metadata attached to ``surface``."""

    metadata = getattr(surface, DATA_RESOURCE_METADATA_ATTR, None)
    if metadata is None:
        return ()
    if isinstance(metadata, data_contract.DataResourceMetadata):
        return (metadata,)
    if isinstance(metadata, tuple) and all(isinstance(item, data_contract.DataResourceMetadata) for item in metadata):
        return metadata
    return ()


def readable_model_field_names(
    metadata: data_contract.DataResourceMetadata,
) -> frozenset[str]:
    """Return concrete model fields projected readably by the GraphQL node."""

    if metadata.model is None or metadata.node_type is None:
        return frozenset()
    readable = {field.name for field in metadata.fields if field.readable}
    names: set[str] = set()
    for model_field in metadata.model._meta.local_fields:
        wire_name = resource_wire_field_name(metadata.node_type, model_field.name)
        if wire_name not in readable:
            continue
        names.add(model_field.name)
        names.add(model_field.attname)
    return frozenset(names)


def make_data_resource_metadata(
    *,
    model: type[models.Model] | None = None,
    roots: data_contract.DataResourceRoots,
    type_names: data_contract.DataResourceTypeNames,
    capabilities: tuple[str, ...],
    node_type: type | None = None,
    filter_type: type | None = None,
    order_type: type | None = None,
    filter_fields: tuple[str, ...] = (),
    order_fields: tuple[str, ...] = (),
    aggregate_fields: tuple[str, ...] = (),
    group_by_fields: tuple[str, ...] = (),
    group_dimensions: tuple[data_contract.DataGroupDimensionMetadata, ...] = (),
    aggregate_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] = (),
    default_measures: tuple[data_contract.DataAggregateMeasureMetadata, ...] = (),
    default_sort: tuple[data_contract.DataDefaultSortMetadata, ...] = (),
    create_input_type: type | None = None,
    update_input_type: type | None = None,
    create_fields: tuple[str, ...] = (),
    update_fields: tuple[str, ...] = (),
    required_create_fields: tuple[str, ...] = (),
    revision_fields: tuple[str, ...] = (),
    relation_axes: tuple[data_contract.DataRelationAxisMetadata, ...] = (),
    group_aliases: tuple[data_contract.DataGroupAliasMetadata, ...] = (),
    lines: data_contract.DataLinesMetadata | None = None,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...] = (),
    subtitle: data_contract.DataResourceSubtitleMetadata | None = None,
    model_label: str | None = None,
    public_id_field: str = PUBLIC_ID_FIELD_NAME,
    row_model: str = "server",
) -> data_contract.DataResourceMetadata:
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
        data_contract.merge_resource_fields(generated_fields, declared_fields)
        if declared_fields
        else generated_fields
    )
    active_fields = require_unique_resource_fields(exposed_model_label, active_fields)
    record_representation = _record_representation_field(active_fields)
    active_subtitle = _resource_subtitle(
        model=model,
        model_label=exposed_model_label,
        node_type=node_type,
        fields=active_fields,
        declared=subtitle,
    )
    return data_contract.DataResourceMetadata(
        model=model,
        model_label=exposed_model_label,
        resource_type=model_resource_type(model) if model is not None else None,
        app_label=app_label,
        model_name=model_name,
        public_id_field=public_id_field,
        roots=roots,
        type_names=type_names,
        canonical_label=canonical_record_model(model)._meta.label if model is not None else None,
        row_model=row_model,
        record_representation=record_representation,
        subtitle=active_subtitle,
        impl_fields=_impl_fields(model, node_type, active_fields),
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
    metadata: data_contract.DataResourceMetadata,
) -> type[_SurfaceT]:
    """Attach model resource metadata to a generated Strawberry surface.

    Only query/mutation/subscription *roots* are scanned for resource
    metadata, so an addon that extends another model's GraphQL *type* (a
    ``type_extensions`` entry adds fields to the node, never to the model's
    resource projection) anchors its contribution on one of its own root
    surfaces — typically its action-mutation bucket — and the per-model merge
    (:func:`angee.data.metadata.merge_data_resources`) folds it into the owning model's resource
    by model label. Fields only a server verb advances are contributed
    read-only (neither creatable nor updatable).
    """

    existing = data_resource_metadata(surface)
    contributor = resource_type_name(surface) or surface.__name__
    attached = dataclasses.replace(metadata, contributors=(contributor,))
    setattr(surface, DATA_RESOURCE_METADATA_ATTR, existing + (attached,))
    return surface


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


_SELECTION_PATH = re.compile(r"^[_A-Za-z][_0-9A-Za-z]*(?:\.[_A-Za-z][_0-9A-Za-z]*)*$")


def _resource_subtitle(
    *,
    model: type[models.Model] | None,
    model_label: str,
    node_type: type | None,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
    declared: data_contract.DataResourceSubtitleMetadata | None,
) -> data_contract.DataResourceSubtitleMetadata | None:
    """Return validated subtitle paths plus canonical timestamp defaults.

    Facts are GraphQL dotted selection paths, rather than model-field paths, so
    an addon may name a nested projection such as ``markdown.word_count``. Exact
    projected model fields carrying Django's ``auto_now_add``/``auto_now``
    flags supply the created/updated defaults; every other semantic fact is
    declared by its owning addon.
    """

    readable = {field.name for field in fields if field.readable}

    def timestamp_path(flag: str) -> str | None:
        if model is None:
            return None
        candidates = tuple(
            wire_name
            for model_field in model._meta.fields
            if getattr(model_field, flag, False)
            if (wire_name := resource_wire_field_name(node_type, model_field.name)) in readable
        )
        if len(candidates) > 1:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} has multiple projected {flag} "
                f"subtitle fields: {', '.join(candidates)}."
            )
        return candidates[0] if candidates else None

    defaults = data_contract.DataResourceSubtitleMetadata(
        created=timestamp_path("auto_now_add"),
        updated=timestamp_path("auto_now"),
    )
    active = data_contract.DataResourceSubtitleMetadata(
        created=(
            declared.created
            if declared is not None and declared.created is not None
            else defaults.created
        ),
        updated=(
            declared.updated
            if declared is not None and declared.updated is not None
            else defaults.updated
        ),
        word_count=declared.word_count if declared is not None else None,
    )
    for field_def in dataclasses.fields(data_contract.DataResourceSubtitleMetadata):
        path = getattr(active, field_def.name)
        if path is not None and _SELECTION_PATH.fullmatch(path) is None:
            raise ImproperlyConfigured(
                f"resource metadata for {model_label} declares invalid subtitle.{field_def.name} "
                f"selection path {path!r}."
            )
        if path is not None:
            require_resource_selection_path(
                node_type,
                path,
                model_label=model_label,
                fact=f"subtitle.{field_def.name}",
            )
    has_fact = any(
        getattr(active, field_def.name) is not None
        for field_def in dataclasses.fields(active)
    )
    return active if has_fact else None


def _record_representation_field(fields: tuple[data_contract.DataResourceFieldMetadata, ...]) -> str | None:
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


def _is_display_scalar(field: data_contract.DataResourceFieldMetadata | None) -> bool:
    """Return whether ``field`` is suitable as a compact record label."""

    return field is not None and field.kind == "scalar" and field.scalar == "String"


def _impl_fields(
    model: type[models.Model] | None,
    node_type: type | None,
    fields: tuple[data_contract.DataResourceFieldMetadata, ...],
) -> tuple[str, ...]:
    """Return this resource's readable ``ImplClassField`` column names, sorted.

    The impl key a row stores is the declared fact a frontend contribution varies
    on per row, so naming the columns that carry one keeps the console from
    hardcoding a model's impl column. Only columns this resource projects are
    named — an impl column the node surface does not expose cannot be read off a
    row. A model may carry several (an MTI parent's and its child's), so this is
    a set, sorted for a deterministic artifact.
    """

    if model is None:
        return ()
    readable = {field.name for field in fields}
    names = {
        resource_wire_field_name(node_type, field.name) or field.name
        for field in model._meta.get_fields()
        if isinstance(field, ImplClassField)
    }
    return tuple(sorted(names & readable))


def _default_sort(
    model: type[models.Model],
    order_fields: tuple[str, ...],
) -> tuple[data_contract.DataDefaultSortMetadata, ...]:
    """Return model default ordering terms exposed by the order input."""

    orderable = set(order_fields)
    sorts: list[data_contract.DataDefaultSortMetadata] = []
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
            data_contract.DataDefaultSortMetadata(
                field=field,
                direction="DESC" if term.startswith("-") else "ASC",
            )
        )
    return tuple(sorts)


def _relation_axes(
    model: type[models.Model],
    group_by_fields: tuple[str, ...],
) -> tuple[data_contract.DataRelationAxisMetadata, ...]:
    """Return direct FK group axes with their related model and optional label axis."""

    label_axes = _relation_label_axes(model, group_by_fields)
    relation_axes: list[data_contract.DataRelationAxisMetadata] = []
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
            data_contract.DataRelationAxisMetadata(
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
        try:
            terminal_field = require_field_for_path(model, path)
        except FieldPathError:
            continue
        if is_to_one_relation(terminal_field):
            # A nested relation is its own identity dimension, not a scalar
            # label for the first relation in the path. Advertising it as a
            # label axis makes clients select the object as a bare leaf.
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
