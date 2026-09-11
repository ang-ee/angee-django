"""Personal explorer intersections, grouping and cross-scope read boundaries."""

from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rebac import actor_context, system_context

from angee.messaging.inbox import InboxCoverage, InboxSearch
from tests.conftest import execute_schema, result_data
from tests.test_messaging import Fragment, Handle, Message, Part, Participant, Party, Thread
from tests.test_nexus import (
    _schema,
    nexus_tables,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("nexus_tables")
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_sections_page_threads_before_bounded_previews():
    owner = User.objects.create_user(username="explorer-sections")
    with system_context(reason="seed explorer"):
        threads = [Thread._base_manager.create(created_by=owner, platform="email") for _ in range(4)]
        for index, thread in enumerate(threads):
            for offset in range(8):
                Message._base_manager.create(
                    created_by=owner, thread=thread, status="synced", sent_at=T0 + timedelta(days=index, minutes=offset)
                )
        standalone = Message._base_manager.create(created_by=owner, status="synced", sent_at=T0 + timedelta(days=5))
        Message._base_manager.create(created_by=owner, status="draft")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        first = inbox.conversations(inbox.results(InboxCoverage(), InboxSearch()), size=2)
        second = inbox.conversations(inbox.messages, size=2, page=2)
        assert (first.count, first.message_count) == (5, 33)
        assert first.rows[0].id == f"message:{standalone.sqid}"
        assert first.rows[1].matching_count == first.rows[1].total_count == 8
        assert len(first.rows[1].messages) == 3
        assert {row.id for row in first.rows}.isdisjoint(row.id for row in second.rows)


def test_confirmed_senders_consolidate_but_suggestions_do_not():
    owner = User.objects.create_user(username="explorer-senders")
    with system_context(reason="seed explorer"):
        party = Party._base_manager.create(created_by=owner, display_name="Ada")
        handles = [
            Handle._base_manager.create(
                created_by=owner, platform="email", value=f"ada{i}@example.com", party=party, party_link_confirmed=i < 2
            )
            for i in range(3)
        ]
        for handle in handles:
            Message._base_manager.create(
                created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=T0
            )
        outbound = Message._base_manager.create(created_by=owner, status="sent", direction="outbound", sent_at=T0)
        for handle in handles[:2]:
            Participant._base_manager.create(created_by=owner, message=outbound, handle=handle, role="to")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        inbound = inbox.senders(InboxCoverage())
        assert {row.id: row.count for row in inbound.rows} == {f"party:{party.sqid}": 2, f"handle:{handles[2].sqid}": 1}
        all_activity = inbox.senders(InboxCoverage(), include_sent=True)
        assert {row.id: row.count for row in all_activity.rows}[f"party:{party.sqid}"] == 3
        assert inbox.results(InboxCoverage(), InboxSearch(), sender=f"party:{party.sqid}").count() == 3


def test_related_is_distinct_cross_scope_and_excludes_unreadable_uses():
    owner = User.objects.create_user(username="explorer-related")
    other = User.objects.create_user(username="explorer-private")
    with system_context(reason="seed explorer"):
        fragment = Fragment._base_manager.create(text="shared invoice text", hash="explorer-shared")
        rows = [
            Message._base_manager.create(created_by=user, status=status, platform=platform, sent_at=T0)
            for user, status, platform in [
                (owner, "synced", "email"),
                (owner, "sent", "slack"),
                (other, "synced", "email"),
                (owner, "draft", "email"),
            ]
        ]
        for row in rows:
            Part._base_manager.create(
                created_by=row.created_by, message=row, role="body", fragment=fragment, position=0
            )
        Part._base_manager.create(created_by=owner, message=rows[0], role="quoted", fragment=fragment, position=1)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        related = inbox.related(f"fragment:{fragment.sqid}")
        assert related.count == related.message_count == 2
        assert {row.pk for row in related.rows} == {row.pk for row in rows[:2]}
        results = inbox.results(InboxCoverage(platforms=["email"]), InboxSearch(text='"shared invoice"'))
        assert list(results.values_list("pk", flat=True)) == [rows[0].pk]


def test_unreadable_thread_is_not_exposed_as_a_group():
    owner = User.objects.create_user(username="explorer-readable")
    other = User.objects.create_user(username="explorer-hidden-thread")
    with system_context(reason="seed explorer"):
        thread = Thread._base_manager.create(created_by=other)
        message = Message._base_manager.create(created_by=owner, thread=thread, status="synced", sent_at=T0)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        result = inbox.conversations(inbox.messages)
        assert result.rows[0].thread is None
        assert result.rows[0].id == f"message:{message.sqid}"


def test_live_schema_projects_sections_and_independent_message():
    owner = User.objects.create_user(username="explorer-schema")
    with system_context(reason="seed explorer"):
        handle = Handle._base_manager.create(created_by=owner, value="sender@example.com", platform="email")
        message = Message._base_manager.create(
            created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=T0
        )
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    result = execute_schema(
        _schema(),
        """query($id: ID!) {
      inbox_senders { count rows { id count handle { id value } } }
      inbox_conversations { count message_count rows { id matching_count messages { id preview parts { id } } } }
      inbox_message(id: $id, coverage: {platforms: ["slack"]}) {
        matches message { id starred } uses { part_id target count }
      }
    }""",
        {"id": str(message.sqid)},
        request=request,
    )
    data = result_data(result)
    assert data["inbox_conversations"]["message_count"] == 1
    assert data["inbox_senders"]["count"] == 1
    assert data["inbox_message"]["matches"] is False
    starred = result_data(
        execute_schema(
            _schema(),
            """mutation($id: ID!) {
      set_inbox_message_starred(id: $id, starred: true) { id starred }
    }""",
            {"id": str(message.sqid)},
            request=request,
        )
    )
    assert starred["set_inbox_message_starred"]["starred"] is True


def test_transcript_anchor_is_bounded_and_can_continue_both_directions():
    owner = User.objects.create_user(username="explorer-anchor")
    with system_context(reason="seed anchored transcript"):
        thread = Thread._base_manager.create(created_by=owner)
        rows = [
            Message._base_manager.create(
                created_by=owner, thread=thread, status="synced", sent_at=T0 + timedelta(minutes=index)
            )
            for index in range(100)
        ]
    with actor_context(owner):
        queryset = Message.objects.inbox().for_thread(thread)
        options = {"scope": ("thread", str(thread.sqid)), "limit": 10}
        page = queryset.feed_page(anchor=f"message:{rows[40].sqid}", **options)
        assert [row.pk for row in page.rows] == [row.pk for row in rows[36:46][::-1]]
        assert page.count == 100 and page.has_older and page.has_newer
        newer = queryset.feed_page(after_cursor=page.newer_cursor, **options)
        older = queryset.feed_page(before_cursor=page.older_cursor, **options)
        assert [row.pk for row in newer.rows] == [row.pk for row in rows[46:56][::-1]]
        assert [row.pk for row in older.rows] == [row.pk for row in rows[26:36][::-1]]
        with pytest.raises(ValueError, match="unavailable"):
            queryset.feed_page(anchor="message:msg_invalid", **options)
