"""A Hasura data resource over a pydantic row model — the computed-source seam.

The non-model sibling of :func:`hasura_model_resource`. ``strawberry-django-
hasura`` owns the dialect mechanics (``hasura_run_query_resource`` + the
in-memory evaluator); this wrapper owns the Angee seam: deriving the GraphQL
node from the pydantic row model (the row-shape SSOT) and attaching the
``angee.resources`` metadata so the frontend drives the computed resource
through the same ``useList`` path as a model resource — no bespoke client path.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Any

import strawberry.experimental.pydantic
from django.core.exceptions import ImproperlyConfigured
from pydantic import BaseModel
from strawberry_django_hasura import (
    HasuraResource,
    InMemoryRowSource,
    RowSource,
    hasura_run_query_resource,
)

from angee.graphql.data.metadata import (
    DataAggregateMeasureMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    resource_type_name,
    resource_wire_field_name,
)


def pydantic_node(row_model: type[BaseModel], *, name: str) -> type:
    """Derive the GraphQL node type from a pydantic row model (all fields).

    The resolver returns the pydantic instances directly (strawberry resolves
    each field by ``getattr``); this holds while the strawberry-exposed field
    types coincide with the pydantic runtime types (no custom conversion).
    """

    return strawberry.experimental.pydantic.type(model=row_model, all_fields=True, name=name)(
        type(name, (), {"__annotations__": {}})
    )


def hasura_pydantic_resource(
    row_model: type[BaseModel],
    *,
    name: str,
    model_label: str,
    filterable: Sequence[str],
    sortable: Sequence[str],
    rows: Callable[[Any], Iterable[BaseModel]] | None = None,
    source: RowSource | None = None,
    node_name: str | None = None,
    id_field: str = "id",
) -> HasuraResource:
    """Build a read-only Hasura resource from a pydantic row model.

    ``row_model`` is the pydantic SSOT for the row shape; the GraphQL node is
    derived from it. ``name`` is the resource stem (plural snake, the list field
    name); ``model_label`` is the dotted ``app.model`` label the frontend keys
    on. ``rows(info) -> Iterable[row_model]`` is the in-memory provider; pass a
    ``source`` instead for a pushdown :class:`RowSource`.

    The attached ``DataResourceMetadata`` carries ``roots.list`` so the frontend
    treats the resource as Hasura-backed and drives it through ``useList``.
    """

    if source is None:
        if rows is None:
            raise TypeError("hasura_pydantic_resource requires rows= or source=")
        source = InMemoryRowSource(rows)
    node = pydantic_node(row_model, name=node_name or row_model.__name__)
    node_type_name = resource_type_name(node)
    resource = hasura_run_query_resource(
        node,
        name=name,
        filterable=list(filterable),
        sortable=list(sortable),
        source=source,
        id_field=id_field,
    )
    if resource.detail_root is None:
        raise ImproperlyConfigured(f"{model_label or name} Hasura resource did not expose a detail root.")
    attach_data_resource_metadata(
        resource.query,
        make_data_resource_metadata(
            model=None,
            model_label=model_label,
            # The computed row is addressed by ``id_field`` — the same fact the
            # library uses for ``<name>_by_pk``; keep them one source of truth.
            public_id_field=id_field,
            node_type=node,
            filter_type=resource.filter_type,
            order_type=resource.order_by_type,
            # Read the wire names off the built query surface (the owner), as the
            # model path does, instead of re-templating the dialect convention.
            roots=DataResourceRoots(
                list_name=resource_wire_field_name(resource.query, str(resource.list_root or name)),
                detail_name=resource_wire_field_name(resource.query, str(resource.detail_root)),
                aggregate_name=resource_wire_field_name(
                    resource.query,
                    str(resource.aggregate_root or f"{name}_aggregate"),
                ),
            ),
            type_names=DataResourceTypeNames(
                query=resource_type_name(resource.query),
                node=node_type_name,
                filter=resource_type_name(resource.filter_type),
                order=resource_type_name(resource.order_by_type),
                aggregate=resource_type_name(resource.aggregate_container_type),
            ),
            capabilities=("list", "detail", "aggregate"),
            # A computed pydantic source is small and admin-only: the frontend
            # fetches it once and filters/sorts/paginates/groups in the browser.
            row_model="client",
            filter_fields=tuple(filterable),
            order_fields=tuple(sortable),
            default_measures=(DataAggregateMeasureMetadata(op="count"),),
        ),
    )
    # The derived node is reachable from the query, but register it in the
    # bundle's types so a schema bucket carries it explicitly (mirrors the
    # model path, where the addon registers the node type).
    return replace(resource, types=[node, *resource.types])
