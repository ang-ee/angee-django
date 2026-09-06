from graphql import build_schema

from angee.data.metadata import DataResourceRoots, DataResourceTypeNames
from angee.graphql.data.final_schema import final_schema_references


def test_final_schema_references_intersect_roots_types_and_capabilities() -> None:
    schema = build_schema(
        """
        type ResourceNode { id: ID! }
        input ResourceFilter { id: ID }
        type ResourceQuery { marker: Boolean }
        type Query {
          resources: [ResourceNode!]!
          resource(id: ID!): ResourceNode
          resource_groups: [String!]!
          resource_groups_count: Int!
          resource_revisions(id: ID!): [String!]!
          resource_query: ResourceQuery
        }
        type Mutation {
          create_resource: ResourceNode!
          preview_resource_delete(id: ID!): Boolean!
        }
        type Subscription { resource_changes: ResourceNode! }
        """
    )
    roots, type_names, capabilities = final_schema_references(
        schema,
        DataResourceRoots(
            list_name="resources",
            detail_name="resource",
            aggregate_name="resources_aggregate",
            group_name="resource_groups",
            group_count_name="resource_groups_count",
            revisions_name="resource_revisions",
            create_name="create_resource",
            update_name="update_resource",
            save_name="save_resource",
            delete_name="delete_resource",
            delete_preview_name="preview_resource_delete",
            changes_name="resource_changes",
        ),
        DataResourceTypeNames(
            query="ResourceQueryFragment",
            node="ResourceNode",
            filter="ResourceFilter",
            order="MissingOrder",
            update_input="MissingUpdateInput",
        ),
    )

    assert roots == DataResourceRoots(
        list_name="resources",
        detail_name="resource",
        group_name="resource_groups",
        group_count_name="resource_groups_count",
        revisions_name="resource_revisions",
        create_name="create_resource",
        delete_preview_name="preview_resource_delete",
        changes_name="resource_changes",
    )
    assert type_names == DataResourceTypeNames(
        query="ResourceQueryFragment",
        node="ResourceNode",
        filter="ResourceFilter",
    )
    assert capabilities == (
        "list",
        "detail",
        "groups",
        "revisions",
        "create",
        "deletePreview",
        "changes",
    )


def test_final_schema_references_use_each_root_operation_owner() -> None:
    schema = build_schema("type Query { shared: String }")

    roots, type_names, capabilities = final_schema_references(
        schema,
        DataResourceRoots(
            list_name="shared",
            create_name="shared",
            changes_name="shared",
        ),
        DataResourceTypeNames(query="MissingQueryFragment", node="MissingNode"),
    )

    assert roots == DataResourceRoots(list_name="shared")
    assert type_names == DataResourceTypeNames(query="MissingQueryFragment")
    assert capabilities == ("list",)
