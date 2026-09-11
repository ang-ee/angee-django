"""Personal explorer intersections, grouping and cross-scope read boundaries."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rebac import actor_context, system_context

from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.inbox_groups import InboxGroupScope
from angee.messaging.inbox_navigator import InboxNavigator, InboxNavigatorOptions
from angee.messaging.inbox_related import InboxRelated
from angee.messaging.inbox_results import InboxResultOptions, InboxResults
from angee.messaging.inbox_transcript import InboxTranscript
from angee.nexus.inbox import NexusInboxNavigator, NexusInboxNavigatorOptions
from tests.conftest import execute_schema, result_data
from tests.test_messaging import Fragment, Handle, Message, Part, Participant, Party, Thread
from tests.test_nexus import (
    _schema,
    nexus_tables,  # noqa: F401
)

pytestmark = pytest.mark.usefixtures("nexus_tables")
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_circle_groups_deduplicate_confirmed_subtrees_and_keep_empty_circles():
    owner = User.objects.create_user(username="explorer-circles")
    Circle = apps.get_model("parties", "Circle")
    CircleMember = apps.get_model("parties", "CircleMember")
    with system_context(reason="seed circle explorer"):
        root = Circle.objects.create(created_by=owner, name="Friends")
        child = Circle.objects.create(created_by=owner, name="Close friends", parent=root)
        empty = Circle.objects.create(created_by=owner, name="Empty")
        ada = Party._base_manager.create(created_by=owner, display_name="Ada")
        other = Party._base_manager.create(created_by=owner, display_name="Suggested")
        for party in (ada, other):
            handle = Handle._base_manager.create(
                created_by=owner,
                platform="email",
                value=f"circle{party.pk}@example.com",
                party=party,
                party_link_confirmed=True,
            )
            Message._base_manager.create(
                created_by=owner, sender=handle, direction="inbound", status="synced", sent_at=T0
            )
        for circle in (root, child):
            CircleMember._base_manager.create(created_by=owner, circle=circle, party=ada, is_confirmed=True)
        CircleMember._base_manager.create(created_by=owner, circle=root, party=other, is_confirmed=False)
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        groups = navigator.groups("circle").page()
        assert groups.record_count == 2
        assert {row.value: row.count for row in groups.rows} == {str(root.sqid): 1, str(child.sqid): 1, None: 1}
        members = navigator.page(scope=InboxGroupScope(axis="circle", value=str(root.sqid)))
        assert [(row.id, row.count) for row in members.rows] == [(f"party:{ada.sqid}", 1)]
        circles = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions(lens="circles"))
        assert {row.id: row.count for row in circles.page().rows} == {
            f"circle:{root.sqid}": 1,
            f"circle:{empty.sqid}": 0,
        }
        assert circles.page(parent=str(root.sqid)).rows[0].id == f"circle:{child.sqid}"
        assert navigator.scoped(navigator.messages, circle=str(root.sqid)).count() == 1


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25)])
def test_coverage_local_days_follow_dst(day, hours):
    start, end = InboxCoverage(start=day, end=day, timezone="Europe/Prague").bounds()
    assert start is not None and end is not None
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == hours * 3600


@pytest.mark.parametrize(
    "values",
    [
        {"timezone": "Invalid/Timezone"},
        {"start": "2026-02-30"},
        {"start": "2026-09-12", "end": "2026-09-11"},
        {"after": "2026-09-11T00:00:00"},
        {"period": "today", "start": "2026-09-11"},
    ],
)
def test_invalid_coverage_cannot_broaden_results(values):
    with pytest.raises(ValueError):
        InboxCoverage(**values)


def test_missing_attachment_bytes_still_match_attachment_filter():
    owner = User.objects.create_user(username="explorer-unavailable-attachment")
    with system_context(reason="seed unavailable attachment"):
        message = Message._base_manager.create(created_by=owner, status="synced", sent_at=T0)
        Part._base_manager.create(created_by=owner, message=message, disposition="attachment", name="missing.pdf")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        assert inbox.matching(inbox.messages, InboxSearch(attachment="any")).count() == 1
        assert inbox.matching(inbox.messages, InboxSearch(attachment="none")).count() == 0


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
        inbound = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions()).page()
        assert {row.id: row.count for row in inbound.rows} == {f"party:{party.sqid}": 2, f"handle:{handles[2].sqid}": 1}
        all_activity = InboxNavigator(
            Message.objects.all(), InboxCoverage(), InboxNavigatorOptions(include_sent=True)
        ).page()
        assert {row.id: row.count for row in all_activity.rows}[f"party:{party.sqid}"] == 3
        assert inbox.results(InboxCoverage(), InboxSearch(), sender=f"party:{party.sqid}").count() == 3
        recipients = InboxNavigator(
            Message.objects.all(), InboxCoverage(), InboxNavigatorOptions(lens="recipients")
        ).page()
        assert [(row.id, row.count) for row in recipients.rows] == [(f"party:{party.sqid}", 1)]
        assert recipients.message_count == 1
        exact_handles = InboxNavigator(
            Message.objects.all(), InboxCoverage(), InboxNavigatorOptions(lens="handles")
        ).page()
        assert {row.id for row in exact_handles.rows} == {f"handle:{handle.sqid}" for handle in handles}


def test_navigator_headers_and_members_page_independently_with_distinct_root_counts():
    owner = User.objects.create_user(username="explorer-platform-groups")
    with system_context(reason="seed independently paged groups"):
        handles = [
            Handle._base_manager.create(created_by=owner, platform="email", value=f"sender{index}@example.com")
            for index in range(27)
        ]
        for handle in handles:
            Message._base_manager.create(
                created_by=owner, sender=handle, status="synced", direction="inbound", platform="email", sent_at=T0
            )
        Message._base_manager.create(
            created_by=owner, sender=handles[0], status="synced", direction="inbound", platform="slack", sent_at=T0
        )
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        first = navigator.groups("platform").page(size=1)
        second = navigator.groups("platform").page(size=1, page=2)
        assert (first.count, first.record_count, first.message_count) == (2, 27, 28)
        assert [(row.value, row.count) for row in first.rows] == [("email", 27)]
        assert [(row.value, row.count) for row in second.rows] == [("slack", 1)]
        leaves = navigator.page(scope=InboxGroupScope(axis="platform", value="email"), page=2)
        assert leaves.count == 27 and len(leaves.rows) == 2
        assert all(row.count == 1 for row in leaves.rows)
        empty_account = navigator.groups("account").page()
        assert [(row.value, row.label, row.count) for row in empty_account.rows] == [(None, "No account", 27)]


def test_recency_classifies_latest_identity_activity_and_keeps_older_messages(monkeypatch):
    owner = User.objects.create_user(username="explorer-recency")
    today = datetime(2026, 9, 11, 12, tzinfo=UTC)
    monkeypatch.setattr("angee.messaging.inbox_navigator.timezone.now", lambda: today)
    with system_context(reason="seed recent and old activity"):
        handle = Handle._base_manager.create(created_by=owner, platform="email", value="recent@example.com")
        for instant in (T0, today):
            Message._base_manager.create(
                created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=instant
            )
    with actor_context(owner):
        navigator = InboxNavigator(
            Message.objects.all(), InboxCoverage(timezone="Europe/Prague"), InboxNavigatorOptions()
        )
        groups = navigator.groups("recency").page()
        assert [(row.value, row.count, row.message_count) for row in groups.rows] == [("today", 1, 2)]
        scoped = navigator.page(scope=InboxGroupScope(axis="recency", value="today"))
        assert scoped.rows[0].count == 2


def test_groups_lens_contains_only_readable_group_chats():
    owner = User.objects.create_user(username="explorer-group-lens")
    other = User.objects.create_user(username="explorer-private-group")
    with system_context(reason="seed group chat lens"):
        threads = [
            Thread._base_manager.create(created_by=user, modality=modality)
            for user, modality in [(owner, "group"), (owner, "direct"), (other, "group")]
        ]
        for thread in threads:
            Message._base_manager.create(created_by=owner, thread=thread, status="synced", sent_at=T0)
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions(lens="groups"))
        page = navigator.page()
        assert page.count == 1
        assert page.rows[0].thread.pk == threads[0].pk
        assert page.rows[0].id == f"thread:{threads[0].sqid}"
        assert navigator.groups("platform").page().record_count == 1


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
        content = InboxResults(inbox, results, InboxResultOptions(lens="text"))
        page = content.page()
        assert page.count == page.message_count == 1
        assert page.rows[0].count == 1 and page.rows[0].part_count == 2
        assert page.rows[0].id == f"fragment:{fragment.sqid}"
        assert content.groups("platform").page().record_count == 1
        assert content.page(scope=InboxGroupScope(axis="platform", value="email")).rows[0].count == 1
        related_target = InboxRelated(inbox, f"fragment:{fragment.sqid}", str(rows[1].sqid))
        assert related_target.page(text="no matching text").count == 0
        assert related_target.summary().count == 2
        assert related_target.summary().source.pk == rows[1].pk


def test_related_relations_authorize_both_endpoints_and_keep_reply_provenance():
    owner = User.objects.create_user(username="explorer-relations")
    other = User.objects.create_user(username="explorer-unreadable-endpoint")
    Edge = apps.get_model("messaging", "MessageEdge")
    with system_context(reason="seed message relations"):
        source = Message._base_manager.create(created_by=owner, status="synced", sent_at=T0)
        reply = Message._base_manager.create(
            created_by=owner, status="synced", parent=source, sent_at=T0 + timedelta(days=1)
        )
        hidden = Message._base_manager.create(created_by=other, status="synced", sent_at=T0)
        edge = Edge._base_manager.create(created_by=owner, src=source, dst=reply, kind="quote")
        Edge._base_manager.create(created_by=owner, src=source, dst=hidden, kind="forward")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        related = InboxRelated(inbox, f"relations:{source.sqid}")
        rows = related.page().rows
        assert related.summary().count == 2
        assert {row.id for row in rows} == {f"edge:{edge.sqid}", f"reply:{reply.sqid}"}
        assert {row.label for row in rows} == {"quote", "reply"}
        assert all(row.src.pk == source.pk and row.dst.pk == reply.pk for row in rows)
        assert inbox.relation_kinds() == ["quote", "reply"]


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
        groups = InboxResults(inbox, inbox.messages, InboxResultOptions()).groups("conversation").page()
        assert groups.rows[0].value == f"message:{message.sqid}"


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
      inbox_navigator { count rows { id label count handle { id } thread { id } } }
      inbox_navigator_groups(axis: "platform") { count record_count rows { value label count } }
      inbox_results { count message_count rows { id latest message { id } } }
      inbox_result_groups(axis: "day") { count record_count rows { value label count } }
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
    assert data["inbox_navigator"]["rows"][0]["label"] == "sender@example.com"
    assert data["inbox_navigator_groups"]["record_count"] == 1
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


def test_transcript_matches_navigate_without_filtering_full_context():
    owner = User.objects.create_user(username="explorer-transcript")
    outsider = User.objects.create_user(username="explorer-transcript-hidden")
    with system_context(reason="seed transcript matches"):
        thread = Thread._base_manager.create(created_by=owner, modality="direct")
        messages = [
            Message._base_manager.create(
                created_by=owner,
                thread=thread,
                status="synced",
                sent_at=T0 + timedelta(minutes=i),
                direction="inbound" if i % 2 == 0 else "outbound",
            )
            for i in range(5)
        ]
        hidden = Message._base_manager.create(created_by=outsider, status="synced", sent_at=T0)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        matching = inbox.matching(inbox.messages, InboxSearch(direction="inbound"))
        transcript = InboxTranscript(inbox, matching, thread.public_id)
        result = transcript.matches(
            visible=[message.public_id for message in messages] + [hidden.public_id], at=messages[2].public_id
        )
        assert result.total == 5
        assert result.count == 3
        assert set(result.visible) == {message.public_id for message in messages[::2]}
        assert result.previous == messages[0].public_id
        assert result.next == messages[4].public_id
        outside = transcript.matches(visible=[], at=messages[1].public_id)
        assert outside.previous == messages[0].public_id
        assert outside.next == messages[2].public_id
        with pytest.raises(ValueError, match="unavailable"):
            transcript.matches(visible=[], at=hidden.public_id)


def test_navigator_selection_label_is_independent_of_finder_and_read_gated():
    owner = User.objects.create_user(username="explorer-selection")
    outsider = User.objects.create_user(username="explorer-selection-hidden")
    with system_context(reason="seed independent selection"):
        handle = Handle._base_manager.create(
            created_by=owner, platform="email", value="ada@example.com", display_name="Ada"
        )
        hidden = Handle._base_manager.create(created_by=outsider, platform="email", value="hidden@example.com")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        selected = inbox.selection(sender=f"handle:{handle.public_id}")
        assert selected.label == "Ada"
        assert selected.handle == handle
        with pytest.raises(ValueError, match="unavailable"):
            inbox.selection(sender=f"handle:{hidden.public_id}")


def test_fading_is_a_persisted_viewer_relative_fact_not_message_age():
    owner = User.objects.create_user(username="explorer-fading")
    Person = apps.get_model("parties", "Person")
    Tie = apps.get_model("nexus", "Tie")
    with system_context(reason="seed persisted viewer fading"):
        viewer = Person._base_manager.create(created_by=owner, user=owner, display_name="Viewer")
        targets = [
            Party._base_manager.create(created_by=owner, display_name=name)
            for name in ("Fading", "Active", "Unrelated")
        ]
        for index, party in enumerate(targets):
            handle = Handle._base_manager.create(
                created_by=owner,
                platform="email",
                value=f"fading{index}@example.com",
                party=party,
                party_link_confirmed=True,
            )
            Message._base_manager.create(
                created_by=owner,
                sender=handle,
                status="synced",
                direction="inbound",
                sent_at=T0 + timedelta(days=100 if index == 0 else 0),
            )
        Tie._base_manager.create(party_a=viewer, party_b=targets[0], is_fading=True)
        Tie._base_manager.create(party_a=viewer, party_b=targets[1], is_fading=False)
        Tie._base_manager.create(party_a=targets[1], party_b=targets[2], is_fading=True)
    with actor_context(owner):
        navigator = NexusInboxNavigator(Message.objects.all(), InboxCoverage(), NexusInboxNavigatorOptions(fading=True))
        assert [(row.id, row.count) for row in navigator.page().rows] == [(f"party:{targets[0].sqid}", 1)]


def test_organization_groups_use_current_readable_edges_and_distinct_root_totals():
    owner = User.objects.create_user(username="explorer-organizations")
    stranger = User.objects.create_user(username="explorer-private-organization")
    Organization = apps.get_model("parties", "Organization")
    Relationship = apps.get_model("parties", "Relationship")
    Kind = apps.get_model("parties", "RelationshipKind")
    with system_context(reason="seed organization memberships"):
        kind = Kind._base_manager.create(slug="explorer-member", name="Member")
        party = Party._base_manager.create(created_by=owner, display_name="Member")
        handle = Handle._base_manager.create(
            created_by=owner, platform="email", value="member@example.com", party=party, party_link_confirmed=True
        )
        Message._base_manager.create(created_by=owner, sender=handle, direction="inbound", status="synced", sent_at=T0)
        organizations = [
            Organization._base_manager.create(
                created_by=owner if index < 3 else stranger, display_name=f"Organization {index}"
            )
            for index in range(4)
        ]
        for index, organization in enumerate(organizations):
            Relationship._base_manager.create(
                created_by=owner,
                party=party,
                other_party=organization,
                kind=kind,
                ended_at=date(2025, 1, 1) if index == 2 else None,
            )
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        groups = navigator.groups("organization").page(size=1)
        assert (groups.count, groups.record_count, groups.message_count) == (2, 1, 1)
        assert groups.rows[0].value == str(organizations[0].sqid)
        assert navigator.groups("organization").page(page=2, size=1).rows[0].value == str(organizations[1].sqid)
        assert navigator.page(scope=InboxGroupScope(axis="organization", value=str(organizations[0].sqid))).count == 1


def test_group_chat_buckets_keep_coverage_totals_without_misclassifying_direct_activity():
    owner = User.objects.create_user(username="explorer-group-direct")
    with system_context(reason="seed covered group and direct activity"):
        handle = Handle._base_manager.create(created_by=owner, platform="email", value="group@example.com")
        group = Thread._base_manager.create(created_by=owner, modality="group")
        for thread in (group, None):
            Message._base_manager.create(
                created_by=owner, thread=thread, sender=handle, direction="inbound", status="synced", sent_at=T0
            )
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        groups = navigator.groups("group").page()
        assert (groups.count, groups.record_count, groups.message_count) == (1, 1, 2)
        assert groups.rows[0].message_count == 1
        assert navigator.page(scope=InboxGroupScope(axis="group", value=None)).count == 0
