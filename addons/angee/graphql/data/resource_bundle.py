"""Project native Hasura resource handles into Angee transport metadata."""

from __future__ import annotations

from functools import partial

from strawberry_django_hasura import HasuraResource

from angee.data.metadata import DataResourceRoots, DataResourceTypeNames
from angee.graphql.data.metadata import resource_type_name, resource_wire_field_name

ResourceQueryMetadata = tuple[DataResourceRoots, DataResourceTypeNames, type | None, type | None]


def resource_query_metadata(resource: HasuraResource) -> ResourceQueryMetadata:
    """Read the built resource's named members, including disabled surfaces."""

    wire_name = partial(resource_wire_field_name, resource.query)
    return (
        DataResourceRoots(
            list_name=wire_name(resource.list_root),
            detail_name=wire_name(resource.detail_root),
            aggregate_name=wire_name(resource.aggregate_root),
            group_name=wire_name(resource.groups_root),
            group_count_name=wire_name(resource.groups_count_root),
        ),
        DataResourceTypeNames(
            query=resource_type_name(resource.query),
            node=resource_type_name(resource.node_type),
            filter=resource_type_name(resource.filter_type),
            order=resource_type_name(resource.order_by_type),
            aggregate=resource_type_name(resource.aggregate_container_type),
            grouped=resource_type_name(resource.group_type),
            group_key=resource_type_name(resource.group_key_type),
            group_by_spec=resource_type_name(resource.group_by_spec_type),
            group_order=resource_type_name(resource.group_order_type),
            having=resource_type_name(resource.having_type),
        ),
        resource.filter_type,
        resource.order_by_type,
    )
