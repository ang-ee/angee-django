"""A read orders its rows by its keys in the priority the client's list gives."""

from __future__ import annotations

from typing import Any

import pytest
import strawberry_django
from rebac import system_context
from strawberry import auto

from angee.graphql.data.hasura import hasura_model_resource
from angee.graphql.node import AngeeNode
from angee.graphql.testing.documents import execute_schema, result_data
from tests.tables import model_tables
from tests.test_aggregates import ResourceParent
from tests.test_group_rows import StatusGroupThing, _schema

# Four rows whose two columns order them differently, created in an order that follows neither: within
# a status the row made first is the earlier name among the closed ones and the later among the open
# ones, so the creation order cannot stand in for a list's second key.
ROWS = (("b", "closed"), ("d", "open"), ("c", "closed"), ("a", "open"))
ORDERS = [
    pytest.param([{"status": "asc"}, {"name": "asc"}], ["b", "c", "a", "d"], id="status, then name"),
    pytest.param([{"name": "asc"}, {"status": "asc"}], ["a", "b", "c", "d"], id="name, then status"),
    pytest.param([{"status": "desc"}, {"name": "desc"}], ["d", "a", "c", "b"], id="status, then name, descending"),
    pytest.param([{"name": "desc"}, {"status": "desc"}], ["d", "c", "b", "a"], id="name, then status, descending"),
]
SORTABLE = [
    pytest.param(["name", "status"], id="declares name first"),
    pytest.param(["status", "name"], id="declares status first"),
]


def _ordered_things_schema(sortable: list[str]) -> Any:
    @strawberry_django.type(StatusGroupThing)
    class OrderedThingType(AngeeNode):
        name: auto
        status: auto

    resource = hasura_model_resource(
        OrderedThingType,
        model=StatusGroupThing,
        name="ordered_things",
        filterable=["id", "name", "status", "parent"],
        sortable=sortable,
        aggregatable=["id"],
        groupable=["parent"],
        get_queryset=lambda info: StatusGroupThing.objects.all(),
        insert=False,
        update=False,
        delete=False,
    )
    return _schema(resource, OrderedThingType)


def _read(sortable: list[str], document: str, order: list[dict[str, str]]) -> Any:
    """The answer to `document` over the four rows, its `$order` sent as a variable, as a client sends it."""

    schema = _ordered_things_schema(sortable)
    with model_tables((ResourceParent, StatusGroupThing)):
        with system_context(reason="test.order_contract.seed"):
            for name, status in ROWS:
                StatusGroupThing.objects.create(name=name, status=status)
        with system_context(reason="test.order_contract.read"):
            return result_data(execute_schema(schema, document, {"order": order}))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("sortable", SORTABLE)
@pytest.mark.parametrize(("order", "expected"), ORDERS)
def test_a_list_read_orders_by_its_keys_in_the_priority_the_list_gives(
    sortable: list[str], order: list[dict[str, str]], expected: list[str],
) -> None:
    """A list read follows its list's priority, whichever order the resource declares its sortable columns in."""

    data = _read(
        sortable,
        "query Ordered($order: [ordered_things_order_by!]) { ordered_things(order_by: $order) { name } }",
        order,
    )
    assert [row["name"] for row in data["ordered_things"]] == expected


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("sortable", SORTABLE)
@pytest.mark.parametrize(("order", "expected"), ORDERS)
def test_a_groups_rows_follow_the_priority_their_list_gives(
    sortable: list[str], order: list[dict[str, str]], expected: list[str],
) -> None:
    """A group's page of rows takes the same list on the groups read, which orders it through its own window."""

    data = _read(
        sortable,
        """
        query Grouped($order: [ordered_things_order_by!]) {
          ordered_things_groups(group_by: [{field: PARENT}]) { rows(limit: 5, order_by: $order) { name } }
        }
        """,
        order,
    )
    assert [[row["name"] for row in group["rows"]] for group in data["ordered_things_groups"]] == [expected]
