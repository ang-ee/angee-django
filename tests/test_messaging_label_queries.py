"""Native SQL budgets for narrow messaging Node label selections."""

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import system_context

from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION
from tests.conftest import execute_schema, result_data
from tests.test_messaging import Fragment, Message, Thread
from tests.test_messaging_graphql import _schema, messaging_graphql_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("messaging_graphql_tables")


@pytest.mark.parametrize("root", ["threads", "messages"])
def test_narrow_messaging_labels_have_constant_queries_and_preserve_permissions(root: str) -> None:
    """The label's owning model fields load with each authorized result page."""

    owner = get_user_model().objects.create_user(username=f"{root}-label-owner")
    outsider = get_user_model().objects.create_user(username=f"{root}-label-outsider")
    at = datetime(2026, 9, 1, tzinfo=UTC)
    with system_context(reason="test.messaging.label.seed"):
        if root == "threads":
            rows = [
                Thread.objects.create(
                    created_by=owner,
                    last_message_at=at - timedelta(minutes=index),
                    title=(
                        None
                        if index == 1
                        else Fragment.objects.upsert(text="" if index == 2 else f"Thread title {index:02}")
                    ),
                )
                for index in range(25)
            ]
            hidden = Thread.objects.create(created_by=outsider, last_message_at=at + timedelta(days=1))
            order = "last_message_at"
            type_name = "ThreadType"
        else:
            rows = [
                Message.objects.create(
                    created_by=owner,
                    sent_at=at - timedelta(minutes=index),
                    preview="" if index == 1 else f"Message preview {index:02}",
                )
                for index in range(25)
            ]
            hidden = Message.objects.create(created_by=outsider, sent_at=at + timedelta(days=1))
            order = "sent_at"
            type_name = "MessageType"
        expected = [{"id": str(row.sqid), "display_name": str(row)} for row in rows]
        assert expected[1]["display_name"] == f"{root.removesuffix('s')}:{rows[1].sqid}"
        if root == "threads":
            assert expected[2]["display_name"] == f"thread:{rows[2].sqid}"

    schema = _schema()
    assert schema._schema.get_type(type_name).fields["display_name"].description == NODE_DISPLAY_NAME_DESCRIPTION
    query = f"""
        query Labels($limit: Int!) {{
          {root}(limit: $limit, order_by: [{{{order}: desc}}]) {{ id display_name }}
        }}
    """
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            payload = result_data(execute_schema(schema, query, {"limit": size}, user=owner))
        assert payload[root] == expected[:size]
        counts.append(len(captured))

    print(f"{root} label SQL counts at 1/25 rows: {counts}")
    assert counts[0] == counts[1], f"Narrow {root} labels must not refetch deferred fields per row: {counts}"

    detail = f"""
        query LabelDetail($id: String!) {{
          {root}_by_pk(id: $id) {{ id display_name }}
        }}
    """
    assert result_data(execute_schema(schema, detail, {"id": str(hidden.sqid)}, user=owner)) == {f"{root}_by_pk": None}


def test_thread_label_and_selected_title_share_native_loading() -> None:
    """The label hint composes with a title selection and its nullable relation."""

    owner = get_user_model().objects.create_user(username="thread-title-label-owner")
    with system_context(reason="test.messaging.label.title.seed"):
        titled = Thread.objects.create(created_by=owner, title=Fragment.objects.upsert(text="Shared title"))
        untitled = Thread.objects.create(created_by=owner)
    schema = _schema()
    query = """
        query Titles {
          threads { id display_name title { id text } }
        }
    """
    rows = result_data(execute_schema(schema, query, user=owner))["threads"]
    assert rows == [
        {
            "id": str(titled.sqid),
            "display_name": "Shared title",
            "title": {"id": str(titled.title.sqid), "text": "Shared title"},
        },
        {"id": str(untitled.sqid), "display_name": f"thread:{untitled.sqid}", "title": None},
    ]
