"""Validate authored data-resource references against a composed schema."""

from __future__ import annotations

import dataclasses

from angee.data import metadata as data_contract
from graphql import GraphQLSchema

__all__ = ["final_schema_references"]


_ROOT_OWNERS = {
    "list_name": "query",
    "detail_name": "query",
    "aggregate_name": "query",
    "group_name": "query",
    "group_count_name": "query",
    "revisions_name": "query",
    "create_name": "mutation",
    "update_name": "mutation",
    "save_name": "mutation",
    "delete_name": "mutation",
    "delete_preview_name": "mutation",
    "changes_name": "subscription",
}

_CAPABILITY_ROOTS = (
    ("list", "list_name"),
    ("detail", "detail_name"),
    ("aggregate", "aggregate_name"),
    ("groups", "group_name"),
    ("revisions", "revisions_name"),
    ("create", "create_name"),
    ("update", "update_name"),
    ("save", "save_name"),
    ("delete", "delete_name"),
    ("deletePreview", "delete_preview_name"),
    ("changes", "changes_name"),
)


def final_schema_references(
    schema: GraphQLSchema,
    roots: data_contract.DataResourceRoots,
    type_names: data_contract.DataResourceTypeNames,
) -> tuple[
    data_contract.DataResourceRoots,
    data_contract.DataResourceTypeNames,
    tuple[str, ...],
]:
    """Intersect roots and final types, retaining the native query-fragment name."""

    root_fields = {
        "query": schema.query_type.fields if schema.query_type is not None else {},
        "mutation": schema.mutation_type.fields if schema.mutation_type is not None else {},
        "subscription": (
            schema.subscription_type.fields if schema.subscription_type is not None else {}
        ),
    }
    validated_roots = dataclasses.replace(
        roots,
        **{
            field_name: None
            for field_name, owner in _ROOT_OWNERS.items()
            if (root_name := getattr(roots, field_name)) is not None
            and root_name not in root_fields[owner]
        },
    )
    validated_type_names = dataclasses.replace(
        type_names,
        **{
            field.name: None
            for field in dataclasses.fields(type_names)
            if field.name != "query"
            and (type_name := getattr(type_names, field.name)) is not None
            and schema.get_type(type_name) is None
        },
    )
    capabilities = tuple(
        capability
        for capability, field_name in _CAPABILITY_ROOTS
        if getattr(validated_roots, field_name) is not None
    )
    return validated_roots, validated_type_names, capabilities
