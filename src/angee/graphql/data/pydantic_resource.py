"""A Hasura data resource over a pydantic row model — the computed-source seam.

The non-model sibling of :func:`hasura_model_resource`. ``strawberry-django-
hasura`` owns the dialect mechanics (``hasura_run_query_resource`` + the
in-memory evaluator); this wrapper owns the Angee seam: deriving the GraphQL
node from the pydantic row model (the row-shape SSOT) and attaching the
``angee.resources`` metadata so the frontend drives the computed resource
through the same ``useList`` path as a model resource — no bespoke client path.
"""

from __future__ import annotations

import types as _types
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Any, Literal, cast

import strawberry
import strawberry.experimental.pydantic
from django.core.exceptions import ImproperlyConfigured
from pydantic import BaseModel
from strawberry.scalars import JSON
from strawberry.types import get_object_definition
from strawberry.types.field import StrawberryField
from strawberry_django.resolvers import django_resolver
from strawberry_django_aggregates import AggregateBuilder, compute_row_aggregation, group_by_alias
from strawberry_django_aggregates.granularity import NumberGranularity
from strawberry_django_hasura import (
    HasuraResource,
    InMemoryRowSource,
    RowSource,
    hasura_run_query_resource,
)

from angee.graphql.data.contracts import DataAggregateMeasureMetadata
from angee.graphql.data.hasura import hasura_query_axes
from angee.graphql.data.metadata import (
    DataResourceContribution,
    DataResourcePolicy,
    attach_data_resource_contribution,
)
from graphql import GraphQLEnumType, GraphQLObjectType, GraphQLScalarType, get_named_type


def pydantic_node(row_model: type[BaseModel], *, name: str, include_computed: bool = False) -> type:
    """Derive the GraphQL node type from a pydantic row model (all fields).

    The resolver returns the pydantic instances directly (strawberry resolves
    each field by ``getattr``); this holds while the strawberry-exposed field
    types coincide with the pydantic runtime types (no custom conversion).
    ``include_computed`` opts into Strawberry's native computed-field support.
    """

    return strawberry.experimental.pydantic.type(
        model=row_model, all_fields=True, name=name, include_computed=include_computed
    )(type(name, (), {"__annotations__": {}}))


def _attach_grouped_rows(
    resource: HasuraResource,
    *,
    node: type,
    source: RowSource,
    groupable: Sequence[str],
) -> None:
    """Page computed buckets using the source and the native group key codec."""

    assert resource.name is not None
    assert resource.row_model is not None
    assert resource.group_key_type is not None
    assert resource.group_by_spec_type is not None
    assert resource.filter_type is not None
    assert resource.order_by_type is not None
    page_type = strawberry.input(
        type(
            f"{resource.name}_grouped_row_page",
            (),
            {"__annotations__": {"key": JSON | None, "limit": int, "offset": int}, "offset": 0},
        ),
        name=f"{resource.name}_grouped_row_page",
    )
    resource.types.append(page_type)
    builder = AggregateBuilder(
        model=resource.row_model,
        name_prefix=resource.name,
        aggregate_fields=[],
        group_by_fields=list(groupable),
    )

    def resolve_grouped_rows(
        self: Any,
        info: strawberry.Info,
        group_by: Any,
        pages: list[Any],
        where: Any = None,
        order_by: Any = None,
    ) -> list[Any]:
        del self
        if len(pages) > 50:
            raise ValueError("A grouped row read accepts at most 50 visible groups.")
        if not pages:
            return []
        spec = builder.translate_group_by([group_by])
        path, granularity = spec[0]
        if path not in groupable:
            raise ValueError(f"Unsupported grouped row key {path!r}.")
        if isinstance(granularity, NumberGranularity):
            raise ValueError("Grouped row pages require a date bucket, not a numeric date part.")
        alias = group_by_alias(path, granularity)
        key_type = info.schema._schema.get_type(get_object_definition(resource.group_key_type, strict=True).name)
        assert isinstance(key_type, GraphQLObjectType)
        scalar = get_named_type(key_type.fields[alias].type)
        assert isinstance(scalar, (GraphQLScalarType, GraphQLEnumType))
        result: list[Any] = []
        keys: list[Any] = []
        for page in pages:
            if page.limit < 0 or page.offset < 0:
                raise ValueError("Grouped row limit and offset must be nonnegative.")
            key = scalar.parse_value(page.key) if page.key is not None else None
            if key in keys:
                raise ValueError("A grouped row read accepts each key once.")
            keys.append(key)
        matching: list[list[Any]] = [[] for _ in pages]
        rows = source.query(info, where=where, order_by=order_by, limit=None, offset=None)
        for row in rows:
            [bucket] = compute_row_aggregation((row,), model=resource.row_model, group_by=spec)
            group_key = builder.shape_group_key(resource.group_key_type, bucket, spec)
            value = getattr(group_key, alias)
            key = scalar.parse_value(scalar.serialize(value)) if value is not None else None
            for index, page_key in enumerate(keys):
                if key == page_key:
                    matching[index].append(row)
                    break
        for page, matched in zip(pages, matching, strict=True):
            result.extend(matched[page.offset : page.offset + page.limit])
        return result

    resolve_grouped_rows.__annotations__ = {
        "self": Any,
        "info": strawberry.Info,
        "group_by": cast(Any, resource.group_by_spec_type),
        "pages": _types.GenericAlias(list, (page_type,)),
        "where": resource.filter_type | None,
        "order_by": _types.GenericAlias(list, (resource.order_by_type,)) | None,
        "return": _types.GenericAlias(list, (node,)),
    }
    root = f"{resource.name}_grouped_rows"
    resolve_grouped_rows.__name__ = root
    field = cast(StrawberryField, strawberry.field(resolver=django_resolver(resolve_grouped_rows), name=root))
    field.python_name = root
    get_object_definition(resource.query, strict=True).fields.append(field)


def hasura_pydantic_resource(
    row_model: type[BaseModel],
    *,
    name: str,
    model_label: str,
    filterable: Sequence[str],
    sortable: Sequence[str],
    groupable: Sequence[str] = (),
    rows: Callable[[Any], Iterable[BaseModel]] | None = None,
    source: RowSource | None = None,
    node_name: str | None = None,
    id_field: str = "id",
    frontend_row_model: Literal["client", "server"] = "client",
    context_fields: Sequence[str] = (),
) -> HasuraResource:
    """Build a read-only Hasura resource from a pydantic row model.

    ``row_model`` is the pydantic SSOT for the row shape; the GraphQL node is
    derived from it. ``name`` is the resource stem (plural snake, the list field
    name); ``model_label`` is the dotted ``app.model`` label the frontend keys
    on. ``rows(info) -> Iterable[row_model]`` is the in-memory provider; pass a
    ``source`` instead for a pushdown :class:`RowSource`, which must return
    every matching row when grouping asks for ``limit=None``.

    ``groupable`` names scalar row columns exposed through the library's
    ``<name>_groups`` roots and the grouped-row page root. Their group axes
    are read from the library's ``HasuraResource.row_model``.

    The attached ``DataResourceMetadata`` carries ``roots.list`` so the frontend
    treats the resource as Hasura-backed and drives it through ``useList``.
    """

    if source is None:
        if rows is None:
            raise TypeError("hasura_pydantic_resource requires rows= or source=")
        source = InMemoryRowSource(rows, id_field=id_field)
    node = pydantic_node(row_model, name=node_name or row_model.__name__)
    resource = hasura_run_query_resource(
        node,
        name=name,
        filterable=list(filterable),
        sortable=list(sortable),
        groupable=list(groupable),
        source=source,
        id_field=id_field,
    )
    if groupable:
        _attach_grouped_rows(resource, node=node, source=source, groupable=groupable)
    if resource.detail_root is None:
        raise ImproperlyConfigured(f"{model_label or name} Hasura resource did not expose a detail root.")
    attach_data_resource_contribution(
        resource.query,
        DataResourceContribution(
            model=None,
            model_label=model_label,
            # The computed row is addressed by ``id_field`` — the same fact the
            # library uses for ``<name>_by_pk``; keep them one source of truth.
            native_resource=resource,
            policy=DataResourcePolicy(
                # Small computed sources may deliberately execute in the browser;
                # larger sources keep filter/sort/page/group execution on the server.
                public_id_field=id_field,
                row_model=frontend_row_model,
                filter_fields=tuple(filterable),
                order_fields=tuple(sortable),
                group_by_fields=tuple(groupable),
                context_fields=tuple(context_fields),
                query_axes=(
                    hasura_query_axes(resource.row_model, tuple(groupable), tuple(filterable))
                    if resource.row_model is not None
                    else ()
                ),
                default_measures=(DataAggregateMeasureMetadata(op="count"),),
            ),
        ),
    )
    # The derived node is reachable from the query, but register it in the
    # bundle's types so a schema bucket carries it explicitly (mirrors the
    # model path, where the addon registers the node type).
    return replace(resource, types=[node, *resource.types])
