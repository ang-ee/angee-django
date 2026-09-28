import strawberry
from graphql import build_schema

from angee.data.metadata import DataMutationArgument, DataResourceRoots, DataResourceTypeNames
from angee.graphql.actions import ActionResult
from angee.graphql.data.final_schema import final_schema_references
from angee.graphql.data.metadata import _finalize_data_resource
from angee.graphql.schema import AngeeSchema


def test_action_result_sdl_exposes_nullable_code() -> None:
    @strawberry.type
    class Query:
        result: ActionResult

    schema = build_schema(AngeeSchema(query=Query).as_str())

    assert str(schema.get_type("ActionResult").fields["code"].type) == "String"


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
          create_resource(object: ResourceFilter!, client_creation_key: String, reason: String): ResourceNode!
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
    metadata = _finalize_data_resource(
        graphql_schema=schema,
        model_label="catalog.resource",
        public_id_field="id",
        roots=roots,
        type_names=type_names,
        capabilities=capabilities,
    )
    assert metadata.create_arguments == ()
    assert metadata.update_arguments == metadata.save_arguments == ()


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


def test_mutation_argument_metadata_uses_final_exposed_root_arguments() -> None:
    schema = build_schema(
        """
        type ResourceNode { id: ID! }
        input ResourceInput { name: String }
        type Query { ready: Boolean! }
        type Mutation {
          create_resource(object: ResourceInput!, client_creation_key: String): ResourceNode!
          update_resource(pk_columns: ID!, _set: ResourceInput!, expected_revision: Int): ResourceNode!
          save_resource(pk: ID!, patch: ResourceInput, lines: [ResourceInput!], expected_revision: Int): ResourceNode!
        }
        """
    )
    metadata = _finalize_data_resource(
        graphql_schema=schema,
        model_label="catalog.resource",
        public_id_field="id",
        roots=DataResourceRoots(
            create_name="create_resource", update_name="update_resource", save_name="save_resource"
        ),
        type_names=DataResourceTypeNames(node="ResourceNode"),
        capabilities=("create", "update", "save"),
        create_argument_names=("client_creation_key", "removed_argument"),
        update_argument_names=("expected_revision",),
        save_argument_names=("expected_revision",),
    )

    assert metadata.create_arguments == (DataMutationArgument("client_creation_key", "String"),)
    assert metadata.update_arguments == metadata.save_arguments == (DataMutationArgument("expected_revision", "Int"),)
