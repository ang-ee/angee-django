"""A groups page carries every group's first page of rows from one windowed read."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import strawberry_django
from django.db import connection, models
from django.test.utils import CaptureQueriesContext
from rebac import RelationshipTuple, system_context, to_object_ref, write_relationships
from strawberry import auto

from angee.base.models.base import AngeeDataModel
from angee.base.pagination import MAX_PAGE_SIZE
from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from tests.conftest import SchemaAddon
from tests.tables import model_tables
from tests.test_aggregates import HasuraJsonResourceThing, HasuraResourceThing, ResourceChild, ResourceParent
from tests.test_grouping_relation_permissions import GroupParent, relation_grouping_case  # noqa: F401


class DatedGroupThing(AngeeDataModel):
    """A row grouped by a date extraction, whose groups carry rows by their range drill."""

    sqid_prefix = "dgt_"

    name = models.CharField(max_length=32)
    happened_at = models.DateTimeField()

    class Meta:
        """Django model options for the test model."""

        app_label = "tests"

CHILD_GROUPS = """
query ChildGroups($where: group_children_bool_exp, $limit: Int!) {
  group_children_groups(
    group_by: [{field: PARENT}, {field: PARENT__NAME}]
    where: $where
    order_by: [{field: "parent__name"}]
  ) {
    key { parent_id parent__name }
    aggregate { count }
    rows(limit: $limit, order_by: [{name: desc}]) { name }
  }
}
"""


def _schema(resource: Any, node: type) -> Any:
    return GraphQLSchemas(
        [SchemaAddon({"public": {"query": [resource.query], "types": [node, *resource.types]}})],
    ).build("public")


def _child_schema() -> Any:
    @strawberry_django.type(ResourceChild)
    class GroupChildType(AngeeNode):
        name: auto

    resource = hasura_model_resource(
        GroupChildType,
        model=ResourceChild,
        name="group_children",
        filterable=["id", "name", "parent"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["parent"],
        get_queryset=lambda info: ResourceChild.objects.all(),
        insert=False,
        update=False,
        delete=False,
    )
    return _schema(resource, GroupChildType)


def _rows_by_parent(data: dict[str, Any]) -> dict[str, list[str]]:
    return {
        group["key"]["parent__name"]: [row["name"] for row in group["rows"]]
        for group in data["group_children_groups"]
    }


@pytest.mark.django_db(transaction=True)
def test_groups_carry_each_groups_first_page_from_one_read() -> None:
    schema = _child_schema()
    with model_tables((ResourceParent, ResourceChild)):
        with system_context(reason="test.group_rows.seed"):
            alpha, beta = (ResourceParent.objects.create(name=name) for name in ("alpha", "beta"))
            for name in ("a1", "a2", "a3"):
                ResourceChild.objects.create(name=name, parent=alpha)
            ResourceChild.objects.create(name="b1", parent=beta)

        with system_context(reason="test.group_rows.read"):
            data = result_data(execute_schema(schema, CHILD_GROUPS, {"limit": 2}))
            assert _rows_by_parent(data) == {"alpha": ["a3", "a2"], "beta": ["b1"]}
            assert [group["aggregate"]["count"] for group in data["group_children_groups"]] == [3, 1]

            # The rows read is one statement whatever the number of groups.
            with CaptureQueriesContext(connection) as one_group:
                execute_schema(schema, CHILD_GROUPS, {"limit": 2, "where": {"name": {"_neq": "b1"}}})
            with CaptureQueriesContext(connection) as two_groups:
                execute_schema(schema, CHILD_GROUPS, {"limit": 2})
            assert len(two_groups) == len(one_group)


@pytest.mark.django_db(transaction=True)
def test_group_rows_follow_the_where_and_respect_the_declared_limit() -> None:
    schema = _child_schema()
    with model_tables((ResourceParent, ResourceChild)):
        with system_context(reason="test.group_rows.seed"):
            alpha = ResourceParent.objects.create(name="alpha")
            for name in ("a1", "a2", "a3"):
                ResourceChild.objects.create(name=name, parent=alpha)

        with system_context(reason="test.group_rows.read"):
            data = result_data(execute_schema(schema, CHILD_GROUPS, {"limit": 5, "where": {"name": {"_neq": "a3"}}}))
            assert _rows_by_parent(data) == {"alpha": ["a2", "a1"]}
            for limit in (0, MAX_PAGE_SIZE + 1):
                result = execute_schema(schema, CHILD_GROUPS, {"limit": limit})
                assert result.errors is not None
                assert f"Page size must be between 1 and {MAX_PAGE_SIZE}" in str(result.errors[0])
                assert result.errors[0].extensions == {"code": "BAD_USER_INPUT"}


@pytest.mark.django_db(transaction=True)
def test_json_path_groups_carry_rows_including_the_missing_key_bucket() -> None:
    @strawberry_django.type(HasuraJsonResourceThing)
    class GroupJsonThingType(AngeeNode):
        name: auto

    resource = hasura_model_resource(
        GroupJsonThingType,
        model=HasuraJsonResourceThing,
        name="group_json_things",
        filterable=["id", "name", "metadata"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["metadata.mailbox"],
        json_paths={"metadata.mailbox": "str"},
        get_queryset=lambda info: HasuraJsonResourceThing.objects.all(),
        insert=False,
        update=False,
        delete=False,
    )
    schema = _schema(resource, GroupJsonThingType)
    with model_tables((HasuraJsonResourceThing,)):
        with system_context(reason="test.group_rows.json.seed"):
            HasuraJsonResourceThing.objects.create(name="one", metadata={"mailbox": "INBOX"})
            HasuraJsonResourceThing.objects.create(name="two", metadata={"mailbox": "Sent"})
            HasuraJsonResourceThing.objects.create(name="three", metadata={"mailbox": "INBOX"})
            HasuraJsonResourceThing.objects.create(name="four", metadata={})

        with system_context(reason="test.group_rows.json.read"):
            data = result_data(execute_schema(schema, """
                query {
                  group_json_things_groups(group_by: [{field: METADATA__MAILBOX}]) {
                    key { metadata__mailbox }
                    rows(limit: 5, order_by: [{name: asc}]) { name }
                  }
                }
            """))
    assert {
        group["key"]["metadata__mailbox"]: [row["name"] for row in group["rows"]]
        for group in data["group_json_things_groups"]
    } == {"INBOX": ["one", "three"], "Sent": ["two"], None: ["four"]}


@pytest.mark.django_db(transaction=True)
def test_group_rows_without_an_order_follow_the_models_meta_ordering() -> None:
    @strawberry_django.type(HasuraResourceThing)
    class GroupOrderedThingType(AngeeNode):
        word_count: auto

    resource = hasura_model_resource(
        GroupOrderedThingType,
        model=HasuraResourceThing,
        name="group_ordered_things",
        filterable=["id", "name"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["name"],
        get_queryset=lambda info: HasuraResourceThing.objects.all(),
        insert=False,
        update=False,
        delete=False,
    )
    schema = _schema(resource, GroupOrderedThingType)
    with model_tables((HasuraResourceThing,)):
        with system_context(reason="test.group_rows.ordered.seed"):
            for name, word_count in (("alpha", 1), ("alpha", 3), ("alpha", 2), ("beta", 5)):
                HasuraResourceThing.objects.create(name=name, word_count=word_count)

        with system_context(reason="test.group_rows.ordered.read"):
            data = result_data(execute_schema(schema, """
                query {
                  group_ordered_things_groups(group_by: [{field: NAME}]) {
                    key { name }
                    rows(limit: 2) { word_count }
                  }
                }
            """))
    # ``Meta.ordering`` is ``-word_count``: the carried page is the list root's, not insertion order.
    assert {
        group["key"]["name"]: [row["word_count"] for row in group["rows"]]
        for group in data["group_ordered_things_groups"]
    } == {"alpha": [3, 2], "beta": [5]}


@pytest.mark.django_db(transaction=True)
def test_a_groups_page_wider_than_the_page_bound_carries_no_rows() -> None:
    schema = _child_schema()
    with model_tables((ResourceParent, ResourceChild)):
        with system_context(reason="test.group_rows.wide.seed"):
            for index in range(MAX_PAGE_SIZE + 1):
                ResourceChild.objects.create(name=f"c{index}", parent=ResourceParent.objects.create(name=f"p{index}"))

        with system_context(reason="test.group_rows.wide.read"):
            counted = result_data(execute_schema(schema, """
                query { group_children_groups(group_by: [{field: PARENT}]) { aggregate { count } } }
            """))
            carried = execute_schema(schema, """
                query { group_children_groups(group_by: [{field: PARENT}]) { rows(limit: 1) { name } } }
            """)
    # Only a groups page that selects rows is bounded; the refusal comes before its rows statement.
    assert len(counted["group_children_groups"]) == MAX_PAGE_SIZE + 1
    assert carried.errors is not None
    assert f"Group rows carry at most {MAX_PAGE_SIZE} groups" in str(carried.errors[0])


def test_a_readers_group_rows_omit_hidden_rows_and_merge_hidden_relations_into_null(
    relation_grouping_case: Any,  # noqa: F811 -- imported shared fixture
) -> None:
    case = relation_grouping_case
    with system_context(reason="test.group_rows.reader.seed"):
        hidden = GroupParent.objects.create(kind="target", target=case.alpha)
    write_relationships([RelationshipTuple(to_object_ref(hidden), "reader", case.bob)])
    data = result_data(execute_schema(case.schema, """
        query {
          group_parents_groups(group_by: [{field: TARGET}], where: {kind: {_eq: "target"}}) {
            key { target_id }
            rows(limit: 5, order_by: [{id: asc}]) { id }
          }
        }
    """, user=case.alice))
    rows = {group["key"]["target_id"]: [row["id"] for row in group["rows"]] for group in data["group_parents_groups"]}
    parents = [str(parent.sqid) for parent in case.parents]
    assert rows == {
        str(case.alpha.sqid): parents[0:2],
        str(case.duplicate_one.sqid): [parents[3]],
        str(case.duplicate_two.sqid): [parents[4]],
        # Beta is hidden from Alice, so its row joins the row without a target.
        None: [parents[2], parents[5]],
    }
    assert str(hidden.sqid) not in {row for page in rows.values() for row in page}


@pytest.mark.django_db(transaction=True)
def test_date_extraction_groups_carry_rows_by_their_range_drill() -> None:
    @strawberry_django.type(DatedGroupThing)
    class DatedGroupThingType(AngeeNode):
        name: auto

    resource = hasura_model_resource(
        DatedGroupThingType,
        model=DatedGroupThing,
        name="dated_group_things",
        filterable=["id", "name", "happened_at"],
        sortable=["name"],
        aggregatable=["id"],
        groupable=["happened_at"],
        get_queryset=lambda info: DatedGroupThing.objects.all(),
        insert=False,
        update=False,
        delete=False,
    )
    schema = _schema(resource, DatedGroupThingType)
    with model_tables((DatedGroupThing,)):
        with system_context(reason="test.group_rows.range.seed"):
            for name, day in (("b", 1), ("a", 1), ("c", 2)):
                DatedGroupThing.objects.create(name=name, happened_at=datetime(2026, 3, day, 12, tzinfo=UTC))

        with system_context(reason="test.group_rows.range.read"):
            data = result_data(execute_schema(schema, """
                query {
                  dated_group_things_groups(group_by: [{field: HAPPENED_AT, granularity: DAY}]) {
                    key { happened_at_day_range { from to } }
                    rows(limit: 5, order_by: [{name: asc}]) { name }
                  }
                }
            """))
    assert sorted([row["name"] for row in group["rows"]] for group in data["dated_group_things_groups"]) == [
        ["a", "b"], ["c"],
    ]
