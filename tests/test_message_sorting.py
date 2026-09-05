"""Native SQL and authorization coverage for rendered inbox sort values."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, current_actor, system_context

from angee.messaging.managers import MessageQuerySet
from tests.conftest import Vendor, execute_schema, make_integration, result_data
from tests.test_messaging import Fragment, Handle, Message, Part, Party, Thread
from tests.test_messaging_graphql import _platform_admin, _schema, messaging_graphql_tables  # noqa: F401

pytestmark = pytest.mark.usefixtures("messaging_graphql_tables")


def test_unused_sender_order_does_not_prepare_identity_scopes() -> None:
    """Unselected sender order must add no directory authorization work."""

    owner = get_user_model().objects.create_user(username="unused-sender-order")
    with system_context(reason="test.messaging.unused_sender_order.seed"):
        message = Message.objects.create(created_by=owner)
    schema = _schema()
    counts = []
    with (
        patch.object(MessageQuerySet, "sender_name_expression", side_effect=AssertionError("Unused sender scope")),
        patch.object(MessageQuerySet, "thread_title_expression", side_effect=AssertionError("Unused thread scope")),
        patch.object(
            MessageQuerySet, "channel_vendor_name_expression", side_effect=AssertionError("Unused channel scope"),
        ),
    ):
        for argument in (
            "", "(order_by: null)", "(order_by: [])",
            "(order_by: [{sender_name: null, thread_title: null, channel_vendor_name: null}])",
        ):
            with CaptureQueriesContext(connection) as captured:
                rows = result_data(execute_schema(schema, f"{{ messages{argument} {{ id }} }}", user=owner))["messages"]
            assert rows == [{"id": str(message.sqid)}]
            counts.append(len(captured))
    print(f"Unselected sender ordering SQL for default/null/empty/null-value: {counts}")
    assert len(set(counts)) == 1


def test_sender_order_uses_current_resolved_arguments() -> None:
    """Literal, variable and merged aliased roots use their own ordering intent."""

    owner = get_user_model().objects.create_user(username="resolved-sender-order")
    with system_context(reason="test.messaging.resolved_sender_order.seed"):
        for label in ("Zulu", "Alpha"):
            handle = Handle.objects.create(created_by=owner, platform="email", value=label)
            Message.objects.create(created_by=owner, sender=handle)
    schema = _schema()
    query = """
      query ResolvedOrder($order: [messages_order_by!]) {
        ascending: messages(order_by: {sender_name: asc}) { sender_name }
        descending: messages(order_by: $order) { id }
        descending: messages(order_by: $order) { sender_name }
        default: messages { id }
      }
    """
    result = result_data(execute_schema(schema, query, {"order": [{"sender_name": "desc"}]}, user=owner))
    assert [row["sender_name"] for row in result["ascending"]] == ["Alpha", "Zulu"]
    assert [row["sender_name"] for row in result["descending"]] == ["Zulu", "Alpha"]
    assert all("id" in row for row in result["descending"])
    assert len(result["default"]) == 2


def test_sender_projection_matches_visible_identity_and_regates_elevated_parents() -> None:
    """Unreadable handles/parties cannot change a shown or sorted sender label."""

    owner = get_user_model().objects.create_user(username="sender-name-owner")
    other = get_user_model().objects.create_user(username="sender-name-other")
    expected = {}
    with system_context(reason="test.messaging.sender_names.seed"):
        party = Party.objects.create(created_by=owner, display_name="Curated name")
        blank_party = Party.objects.create(created_by=owner, display_name="")
        hidden_party = Party.objects.create(created_by=other, display_name="Hidden curated name")
        cases = [
            (party, True, "Envelope", owner, "Curated name"),
            (party, False, "Envelope", owner, "Envelope"),
            (blank_party, True, "Envelope", owner, "Envelope"),
            (hidden_party, True, "Visible envelope", owner, "Visible envelope"),
            (None, False, "", owner, "sender-4@example.com"),
            (party, True, "Hidden envelope", other, ""),
        ]
        for index, (linked, confirmed, envelope, creator, label) in enumerate(cases):
            handle = Handle.objects.create(
                created_by=creator,
                platform="email",
                value=f"sender-{index}@example.com",
                party=linked,
                party_link_confirmed=confirmed,
                display_name=envelope,
            )
            message = Message.objects.create(created_by=owner, sender=handle)
            expected[str(message.sqid)] = label
        missing = Message.objects.create(created_by=owner)
        expected[str(missing.sqid)] = ""
        hidden = Message.objects.create(created_by=other, sender=handle)

    payload = result_data(
        execute_schema(_schema(), "{ messages(order_by: [{sender_name: asc}]) { id sender_name } }", user=owner)
    )
    assert {row["id"]: row["sender_name"] for row in payload["messages"]} == expected
    assert [row["sender_name"] for row in payload["messages"]] == sorted(expected.values())
    assert str(hidden.sqid) not in expected
    with actor_context(owner), system_context(reason="test.messaging.sender_names.elevated_parent"):
        rows = Message._base_manager.filter(sqid__in=expected).annotate(
            _sender_name=Message.objects.sender_name_expression(),
        )
        assert {str(row.sqid): row.sender_name() for row in rows} == expected
        # A materialized parent without optimizer annotations uses the same
        # Handle owner, including the denied sender and missing-sender cases.
        assert {str(row.sqid): row.sender_name() for row in Message._base_manager.filter(sqid__in=expected)} == expected
    # Native queryset materialization pins its actor on each model, even after
    # leaving GraphQL's ambient context. The fallback must preserve that owner.
    assert current_actor() is None
    assert {
        str(row.sqid): row.sender_name()
        for row in Message.objects.with_actor(owner).filter(sqid__in=expected)
    } == expected


def test_sender_sorted_pages_use_the_selected_scalar_with_bounded_sql() -> None:
    """Equal labels page deterministically and per-author SQL remains bounded."""

    owner = get_user_model().objects.create_user(username="sender-sort-owner")
    expected = []
    with system_context(reason="test.messaging.sender_sort.seed"):
        for index in range(25):
            label = f"Person {24 - index:02}" if index < 20 else "Same name"
            party = Party.objects.create(created_by=owner, display_name=label)
            handle = Handle.objects.create(
                created_by=owner,
                platform="email",
                value=f"sort-{index}@example.com",
                party=party,
                party_link_confirmed=True,
                display_name=f"Envelope {index:02}",
            )
            message = Message.objects.create(created_by=owner, sender=handle)
            expected.append((label, message.pk, str(message.sqid)))
    expected.sort()
    schema = _schema()
    query = """
      query SenderPage($limit: Int!, $offset: Int!) {
        messages(limit: $limit, offset: $offset, order_by: [{sender_name: asc}]) {
          id sender_name
        }
      }
    """
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size, "offset": 0}, user=owner))["messages"]
        assert rows == [{"id": public_id, "sender_name": label} for label, _, public_id in expected[:size]]
        counts.append(len(captured))
    print(f"Sender ordered projection SQL at 1/25 rows: {counts}")
    assert counts[0] == counts[1], f"Sender identities must load in SQL, without per-row reads: {counts}"

    paged = []
    for offset in range(0, 25, 7):
        paged.extend(result_data(execute_schema(schema, query, {"limit": 7, "offset": offset}, user=owner))["messages"])
    assert [row["id"] for row in paged] == [public_id for _, _, public_id in expected]
    descending = query.replace("sender_name: asc", "sender_name: desc")
    reversed_labels = result_data(execute_schema(schema, descending, {"limit": 25, "offset": 0}, user=owner))[
        "messages"
    ]
    assert [row["sender_name"] for row in reversed_labels] == sorted((label for label, _, _ in expected), reverse=True)


def test_title_sort_uses_the_existing_title_part_projection() -> None:
    """The row title and its sort value come from the same title annotation."""

    owner = get_user_model().objects.create_user(username="title-sort-owner")
    expected = []
    with system_context(reason="test.messaging.title_sort.seed"):
        for title in ("Zulu", "Alpha", ""):
            message = Message.objects.create(created_by=owner, sent_at=datetime(2026, 9, 1, tzinfo=UTC))
            if title:
                Part.objects.create(
                    created_by=owner,
                    message=message,
                    role=Part.PartRole.TITLE,
                    fragment=Fragment.objects.upsert(text=title),
                )
            expected.append((title, str(message.sqid)))
    rows = result_data(execute_schema(_schema(), "{ messages(order_by: [{title: asc}]) { id title } }", user=owner))[
        "messages"
    ]
    assert rows == [{"id": public_id, "title": title} for title, public_id in sorted(expected)]


def test_related_sort_values_ignore_denied_labels_and_regate_elevated_parents() -> None:
    """Unreadable related labels cannot affect visible values or page order."""

    owner = get_user_model().objects.create_user(username="related-sort-owner")
    other = get_user_model().objects.create_user(username="related-sort-other")
    with system_context(reason="test.messaging.related_sort.seed"):
        visible = Thread.objects.create(created_by=owner, title=Fragment.objects.upsert(text="Visible thread"))
        hidden = Thread.objects.create(created_by=other, title=Fragment.objects.upsert(text="Alpha secret"))
        channel = make_integration("related-sort-channel", owner=owner)
        other_channel = make_integration("related-sort-other-channel", owner=other)
        rows = [
            Message.objects.create(created_by=owner, thread=visible, channel=channel),
            Message.objects.create(created_by=owner, thread=hidden, channel=other_channel),
            Message.objects.create(created_by=owner),
        ]
    schema = _schema()
    query = """{
      messages(order_by: [{thread_title: asc}, {channel_vendor_name: desc}]) {
        id thread_title channel_vendor_name
      }
    }"""
    # Vendor catalogue reads require platform admin. A visible Integration is
    # insufficient on its own, and the other Integration is independently denied.
    expected = [
        {"id": str(rows[1].sqid), "thread_title": "", "channel_vendor_name": ""},
        {"id": str(rows[2].sqid), "thread_title": "", "channel_vendor_name": ""},
        {"id": str(rows[0].sqid), "thread_title": "Visible thread", "channel_vendor_name": ""},
    ]
    assert result_data(execute_schema(schema, query, user=owner))["messages"] == expected
    with system_context(reason="test.messaging.related_sort.hidden_change"):
        hidden.title = Fragment.objects.upsert(text="Zulu secret")
        hidden.save(update_fields=["title"])
        Vendor.objects.filter(pk__in=[channel.vendor_id, other_channel.vendor_id]).update(display_name="Changed secret")
    assert result_data(execute_schema(schema, query, user=owner))["messages"] == expected
    with actor_context(owner), system_context(reason="test.messaging.related_sort.elevated_parent"):
        materialized = Message._base_manager.filter(pk__in=[row.pk for row in rows]).order_by("pk")
        assert [(row.thread_title(), row.channel_vendor_name()) for row in materialized] == [
            ("Visible thread", ""), ("", ""), ("", ""),
        ]
    for old_axis in ("thread__title__text", "channel__vendor__display_name"):
        result = execute_schema(schema, "{ messages(order_by: [{" + old_axis + ": asc}]) { id } }", user=owner)
        assert result.errors and "not defined by type 'messages_order_by'" in result.errors[0].message


def test_related_selected_sort_values_have_bounded_sql_and_native_ties() -> None:
    """Visible Thread/Integration/Vendor projections stay in the row query."""

    owner = _platform_admin("related-sort-admin")
    expected = []
    with system_context(reason="test.messaging.related_sort.cost_seed"):
        for index in range(25):
            title = f"Thread {index % 3}"
            vendor_name = f"Vendor {index % 4}"
            thread = Thread.objects.create(created_by=owner, title=Fragment.objects.upsert(text=title))
            channel = make_integration(f"related-sort-cost-{index}", owner=owner)
            Vendor.objects.filter(pk=channel.vendor_id).update(display_name=vendor_name)
            message = Message.objects.create(created_by=owner, thread=thread, channel=channel)
            expected.append((title, vendor_name, message.pk, str(message.sqid)))
    expected.sort()
    schema = _schema()
    query = """query RelatedPage($limit: Int!, $offset: Int!) {
      messages(limit: $limit, offset: $offset,
        order_by: [{thread_title: asc}, {channel_vendor_name: asc}, {sender_name: asc}, {title: asc}]) {
        id thread_title channel_vendor_name sender_name title
      }
    }"""
    counts = []
    for size in (1, 25):
        with CaptureQueriesContext(connection) as captured:
            rows = result_data(execute_schema(schema, query, {"limit": size, "offset": 0}, user=owner))["messages"]
        counts.append(len(captured))
        assert rows == [
            {"id": public_id, "thread_title": title, "channel_vendor_name": vendor_name, "sender_name": "", "title": ""}
            for title, vendor_name, _, public_id in expected[:size]
        ]
    print(f"Related ordered projection SQL at 1/25 rows: {counts}")
    assert counts[0] == counts[1], f"Related labels must add no per-row reads: {counts}"
    paged = []
    for offset in range(0, 25, 7):
        paged.extend(result_data(execute_schema(schema, query, {"limit": 7, "offset": offset}, user=owner))["messages"])
    assert [row["id"] for row in paged] == [public_id for _, _, _, public_id in expected]
    descending = query.replace("thread_title: asc", "thread_title: desc").replace(
        "channel_vendor_name: asc", "channel_vendor_name: desc",
    )
    rows = result_data(execute_schema(schema, descending, {"limit": 25, "offset": 0}, user=owner))["messages"]
    assert [(row["thread_title"], row["channel_vendor_name"]) for row in rows] == sorted(
        ((title, vendor_name) for title, vendor_name, _, _ in expected), reverse=True,
    )
