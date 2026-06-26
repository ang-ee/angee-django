"""Hasura data-resource schema helpers composed from Strawberry-native primitives."""

from angee.graphql.data.hasura import (
    AngeeHasuraWriteBackend,
    aggregate_queryset,
    attach_hasura_resource_metadata,
    declared_hasura_resource_fields,
    hasura_model_resource,
    public_pk_decoder,
)
from angee.graphql.data.metadata import (
    DataRelationAxisMetadata,
    DataResourceFieldMetadata,
    DataResourceMetadata,
    DataResourceRoots,
    DataResourceTypeNames,
    resource_type_name,
    resource_wire_field_name,
    resource_wire_field_names,
)
from angee.graphql.data.pydantic_resource import (
    hasura_pydantic_resource,
    pydantic_node,
)

__all__ = [
    "AngeeHasuraWriteBackend",
    "aggregate_queryset",
    "DataRelationAxisMetadata",
    "DataResourceFieldMetadata",
    "DataResourceMetadata",
    "DataResourceRoots",
    "DataResourceTypeNames",
    "declared_hasura_resource_fields",
    "hasura_model_resource",
    "hasura_pydantic_resource",
    "public_pk_decoder",
    "pydantic_node",
    "attach_hasura_resource_metadata",
    "resource_type_name",
    "resource_wire_field_name",
    "resource_wire_field_names",
]
