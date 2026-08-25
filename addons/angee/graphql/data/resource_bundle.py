"""Generated Hasura resource bundle inspection helpers."""

from __future__ import annotations

from functools import partial
from typing import Any

from angee.data.metadata import DataResourceRoots, DataResourceTypeNames
from strawberry_django_hasura import HasuraResource

from angee.graphql.data.metadata import resource_type_name, resource_wire_field_name

ResourceQueryMetadata = tuple[DataResourceRoots, DataResourceTypeNames, type | None, type | None]


def resource_attr(resource: HasuraResource, name: str, fallback: Any) -> Any:
    """Return a HasuraResource attribute, falling back for older package shapes."""

    return getattr(resource, name, fallback)


def resource_type_by_name(resource: HasuraResource, name: str) -> type | None:
    """Return the generated type with GraphQL name ``name`` when present."""

    return next(
        (item for item in resource.types if resource_type_name(item) == name),
        None,
    )


def resource_type_by_suffix(resource: HasuraResource, suffix: str) -> type | None:
    """Return the single generated type whose GraphQL name ends with ``suffix``."""

    matches = [
        item
        for item in resource.types
        if (resource_type_name(item) or "").endswith(suffix)
    ]
    return matches[0] if len(matches) == 1 else None


def resource_query_metadata(
    resource: HasuraResource,
    *,
    name: str,
    node_type: type,
    grouped: bool = False,
) -> ResourceQueryMetadata:
    """Reconstruct query roots and types from a generated resource bundle."""

    list_root = resource_attr(resource, "list_root", name)
    detail_root = resource_attr(resource, "detail_root", f"{name}_by_pk")
    aggregate_root = resource_attr(resource, "aggregate_root", f"{name}_aggregate")
    groups_root = resource_attr(resource, "groups_root", f"{name}_groups")
    groups_count_root = resource_attr(resource, "groups_count_root", f"{name}_groups_count")
    query_types = {
        key: resource_attr(resource, attr, finder(resource, fallback) if enabled else None)
        for key, attr, finder, fallback, enabled in (
            ("filter", "filter_type", resource_type_by_name, f"{name}_bool_exp", True),
            ("order", "order_by_type", resource_type_by_name, f"{name}_order_by", True),
            ("aggregate", "aggregate_container_type", resource_type_by_name, f"{name}_aggregate", True),
            ("grouped", "group_type", resource_type_by_name, f"{name}_group", grouped),
            ("group_key", "group_key_type", resource_type_by_suffix, "GroupKey", grouped),
            ("group_by_spec", "group_by_spec_type", resource_type_by_suffix, "GroupBySpec", grouped),
            ("group_order", "group_order_type", resource_type_by_suffix, "GroupOrder", grouped),
            ("having", "having_type", resource_type_by_suffix, "Having", grouped),
        )
    }
    wire_name = partial(resource_wire_field_name, resource.query)
    return (
        DataResourceRoots(
            list_name=wire_name(str(list_root or name)),
            detail_name=wire_name(None if detail_root is None else str(detail_root)),
            aggregate_name=wire_name(str(aggregate_root or f"{name}_aggregate")),
            group_name=wire_name(str(groups_root)) if grouped and groups_root is not None else None,
            group_count_name=wire_name(str(groups_count_root)) if grouped and groups_count_root is not None else None,
        ),
        DataResourceTypeNames(
            query=resource_type_name(resource.query),
            node=resource_type_name(node_type),
            **{key: resource_type_name(value) for key, value in query_types.items()},
        ),
        query_types["filter"],
        query_types["order"],
    )
