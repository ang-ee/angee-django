"""Personal explorer intersections, grouping and cross-scope read boundaries."""

from datetime import UTC, date, datetime, timedelta

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.inbox_groups import InboxGroupScope
from angee.messaging.inbox_navigator import InboxNavigator, InboxNavigatorOptions
from angee.messaging.inbox_related import InboxRelated
from angee.messaging.inbox_results import InboxResultOptions, InboxResults
from angee.messaging.inbox_transcript import InboxTranscript
from angee.messaging.testing.models import Channel, Fragment, Handle, Message, Part, Participant, Party, Thread
from angee.nexus.inbox import NexusInboxNavigator, NexusInboxNavigatorOptions
from tests.conftest import execute_schema, make_integration, result_data
from tests.test_messaging import _storage_drive
from tests.test_nexus import _schema

pytestmark = pytest.mark.usefixtures("composed_tables")
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_same_named_conversations_keep_identity_and_show_source_context():
    owner = User.objects.create_user(username="explorer-conversation-context")
    account = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed distinct same-named conversations"):
        title = Fragment.objects.upsert(text="Same person", created_by_id=owner.pk)
        threads = [Thread._base_manager.create(
            created_by=owner, channel=account, title=title, platform="email",
        ) for _ in range(2)]
        for index, thread in enumerate(threads):
            Message._base_manager.create(
                created_by=owner, channel=account, thread=thread, status="synced",
                sent_at=T0 + timedelta(days=index),
            )
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        groups = InboxResults(inbox, inbox.messages, InboxResultOptions()).groups("conversation").page()
        assert groups.count == 2
        assert [row.value for row in groups.rows] == [f"thread:{thread.sqid}" for thread in reversed(threads)]
        assert [row.label for row in groups.rows] == ["Same person", "Same person"]
        assert [row.latest for row in groups.rows] == [T0 + timedelta(days=1), T0]
        assert all(row.platform == "email" and row.account == account.display_name for row in groups.rows)


def test_file_backlinks_allow_empty_files_and_deduplicate_authorized_messages(tmp_path):
    owner = User.objects.create_user(username="explorer-file-uses")
    other = User.objects.create_user(username="explorer-file-hidden")
    account = make_integration(owner.username, model=Channel, owner=owner)
    hidden_account = make_integration(other.username, model=Channel, owner=other)
    File = apps.get_model("storage", "File")
    with system_context(reason="seed readable file without message uses"):
        drive = _storage_drive(tmp_path, owner=owner)
        file = File.objects.create(drive=drive, created_by=owner, filename="brief.pdf", content_hash="a" * 64)
    target = f"file:{file.sqid}"
    with actor_context(owner):
        empty = InboxRelated(Message.objects.all().explorer(), target).page()
        assert (empty.rows, empty.count, empty.message_count) == ([], 0, 0)
    with system_context(reason="seed repeated, hidden and draft file uses"):
        messages = [Message._base_manager.create(
            created_by=other if index == 2 else owner,
            channel=hidden_account if index == 2 else account,
            status="draft" if index == 3 else "synced", sent_at=T0 + timedelta(days=index),
        ) for index in range(4)]
        for message in messages:
            for position in range(2):
                Part._base_manager.create(
                    created_by=message.created_by, message=message, file=file,
                    name="original-brief.pdf", disposition="attachment", position=position,
                )
    with actor_context(owner):
        related = InboxRelated(Message.objects.all().explorer(), target, str(messages[0].sqid))
        uses = Message.objects.all().explorer().message_uses(str(messages[0].sqid))
        assert [(use.target, use.count) for use in uses] == [(target, 2), (target, 2)]
        assert related.summary().count == 2
        first, second = related.page(size=1), related.page(page=2, size=1)
        assert (first.count, first.message_count) == (2, 2)
        assert [row.message.pk for row in [*first.rows, *second.rows]] == [messages[1].pk, messages[0].pk]
        assert all(row.part.file_id == file.pk for row in [*first.rows, *second.rows])
    with actor_context(other):
        with pytest.raises(ValueError, match="Related content unavailable"):
            InboxRelated(Message.objects.all().explorer(), target).page()


def test_circle_groups_deduplicate_confirmed_subtrees_and_keep_empty_circles():
    owner = User.objects.create_user(username="explorer-circles")
    Circle = apps.get_model("parties", "Circle")
    CircleMember = apps.get_model("parties", "CircleMember")
    channel = make_integration(owner.username, model=Channel, owner=owner)
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
                channel=channel,
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed unavailable attachment"):
        message = Message._base_manager.create(channel=channel, created_by=owner, status="synced", sent_at=T0)
        Part._base_manager.create(created_by=owner, message=message, disposition="attachment", name="missing.pdf")
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        assert inbox.matching(inbox.messages, InboxSearch(attachment="any")).count() == 1
        assert inbox.matching(inbox.messages, InboxSearch(attachment="none")).count() == 0


def test_search_candidates_preserve_terms_roles_and_distinct_message_uses():
    owner = User.objects.create_user(username="explorer-search-owner")
    other = User.objects.create_user(username="explorer-search-other")
    other_channel = make_integration(other.username, model=Channel, owner=other)
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed indexed search candidates"):
        document = Fragment.objects.upsert(text="document", created_by_id=owner.pk)
        alpha = Fragment.objects.upsert(text="alpha", created_by_id=owner.pk)
        phrase = Fragment.objects.upsert(text="document alpha", created_by_id=owner.pk)
        public = Thread._base_manager.create(created_by=owner, modality="public_thread")
        messages = {
            name: Message._base_manager.create(
                channel=other_channel if name == "hidden" else channel,
                created_by=other if name == "hidden" else owner,
                status="draft" if name == "draft" else "synced",
                thread=public if name == "public" else None,
                sent_at=T0,
            )
            for name in ("text", "shared", "quoted", "filename", "inline", "hidden", "draft", "public")
        }
        for name in ("text", "shared", "hidden", "draft", "public"):
            for position, fragment in enumerate((document, alpha, document)):
                Part._base_manager.create(
                    created_by=owner, message=messages[name], role="body", fragment=fragment, position=position,
                )
        Part._base_manager.create(created_by=owner, message=messages["quoted"], role="quoted", fragment=phrase)
        for name in ("text", "filename", "inline"):
            Part._base_manager.create(
                created_by=owner, message=messages[name], name="document alpha.pdf",
                disposition="inline" if name == "inline" else "attachment", position=10,
            )
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        matches = inbox.results(InboxCoverage(), InboxSearch(text="document alpha"))
        assert set(matches.values_list("pk", flat=True)) == {
            messages[name].pk for name in ("text", "shared", "filename")
        }
        assert matches.count() == 3
        quoted = inbox.results(InboxCoverage(), InboxSearch(text='"document alpha"', quoted=True))
        assert set(quoted.values_list("pk", flat=True)) == {
            messages[name].pk for name in ("text", "quoted", "filename")
        }
        assert inbox.results(InboxCoverage(), InboxSearch(text="document missing-token")).count() == 0
        groups = InboxResults(inbox, matches, InboxResultOptions()).groups("conversation").page()
        assert (groups.count, groups.record_count, groups.message_count) == (3, 3, 3)


def test_sections_page_threads_before_bounded_previews():
    owner = User.objects.create_user(username="explorer-sections")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed explorer"):
        threads = [Thread._base_manager.create(created_by=owner, platform="email") for _ in range(4)]
        for index, thread in enumerate(threads):
            for offset in range(8):
                Message._base_manager.create(
                    created_by=owner, thread=thread, status="synced", sent_at=T0 + timedelta(days=index, minutes=offset)
                )
        standalone = Message._base_manager.create(
            channel=channel, created_by=owner, status="synced", sent_at=T0 + timedelta(days=5)
        )
        Message._base_manager.create(channel=channel, created_by=owner, status="draft")
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
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
                channel=channel,
                created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=T0
            )
        outbound = Message._base_manager.create(
            channel=channel, created_by=owner, status="sent", direction="outbound", sent_at=T0
        )
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
        exact = InboxSearch(handle=str(handles[0].sqid))
        assert inbox.results(InboxCoverage(), exact, sender=f"handle:{handles[0].sqid}").count() == 2
        both = inbox.results(InboxCoverage(), exact, sender=f"handle:{handles[1].sqid}")
        assert list(both.values_list("pk", flat=True)) == [outbound.pk]
        assert inbox.results(InboxCoverage(before=T0), exact, sender=f"party:{party.sqid}").count() == 0


def test_navigator_headers_and_members_page_independently_with_distinct_root_counts():
    owner = User.objects.create_user(username="explorer-platform-groups")
    with system_context(reason="seed parent thread"):
        thread = Thread._base_manager.create(owner=owner, created_by=owner)
    with system_context(reason="seed independently paged groups"):
        handles = [
            Handle._base_manager.create(created_by=owner, platform="email", value=f"sender{index}@example.com")
            for index in range(27)
        ]
        for handle in handles:
            Message._base_manager.create(
                thread=thread,
                created_by=owner, sender=handle, status="synced", direction="inbound", platform="email", sent_at=T0
            )
        Message._base_manager.create(
            thread=thread,
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


def test_group_windows_keep_exact_totals_on_empty_and_out_of_range_pages():
    owner = User.objects.create_user(username="explorer-window-counts")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed paged activity"):
        handle = Handle._base_manager.create(created_by=owner, platform="email", value="window@example.com")
        message = Message._base_manager.create(
            channel=channel,
            created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=T0
        )
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        inbox = Message.objects.all().explorer()
        groups = InboxResults(inbox, inbox.messages, InboxResultOptions()).groups("conversation")
        for page in (1, 2, 20):
            result = groups.page(page=page, size=1)
            senders = navigator.page(page=page, size=1)
            assert (result.count, result.record_count, result.message_count) == (1, 1, 1)
            assert (senders.count, senders.message_count) == (1, 1)
            assert len(result.rows) == len(senders.rows) == (1 if page == 1 else 0)
        empty = InboxResults(inbox, inbox.messages.exclude(pk=message.pk), InboxResultOptions())
        for page in (1, 2):
            result = empty.groups("conversation").page(page=page)
            assert (result.rows, result.count, result.record_count, result.message_count) == ([], 0, 0, 0)


def test_message_group_totals_span_pages_and_conversation_titles_remain_exact():
    owner = User.objects.create_user(username="explorer-partition-totals")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed disjoint message groups"):
        title = Fragment.objects.upsert(text="A document conversation", created_by_id=owner.pk)
        thread = Thread._base_manager.create(created_by=owner, title=title)
        untitled = Thread._base_manager.create(created_by=owner)
        for index, conversation in enumerate((thread, thread, untitled, None)):
            Message._base_manager.create(
                channel=channel,
                created_by=owner, thread=conversation, status="synced", direction="inbound",
                platform="email" if index < 2 else "slack", sent_at=T0 + timedelta(days=index),
            )
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        results = InboxResults(inbox, inbox.messages, InboxResultOptions())
        for axis, count in (("conversation", 3), ("platform", 2), ("account", 1), ("day", 4)):
            groups = results.groups(axis)
            for page in (1, 2, 20):
                result = groups.page(page=page, size=1)
                assert (result.count, result.record_count, result.message_count) == (count, 4, 4)
            assert sum(row.message_count for row in groups.page().rows) == 4
        conversations = results.groups("conversation").page()
        # An untitled thread with no known people is unlabelled; clients translate it.
        assert [row.label for row in conversations.rows] == [
            "Standalone message", "", "A document conversation",
        ]
        assert [row.message_count for row in conversations.rows] == [1, 1, 2]


def test_navigator_name_fallbacks_and_recipient_overlap_keep_exact_totals():
    owner = User.objects.create_user(username="explorer-name-totals")
    other = User.objects.create_user(username="explorer-private-name")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed readable names and overlapping recipients"):
        blank = Party._base_manager.create(created_by=owner, display_name="")
        private = Party._base_manager.create(created_by=other, display_name="Hidden party name")
        handles = [
            Handle._base_manager.create(
                created_by=owner, platform="email", value=f"label{index}@example.com", display_name=name,
                party=blank if index < 2 else private if index == 2 else None, party_link_confirmed=True,
            )
            for index, name in enumerate(("Zulu", "Alpha", "Beta", "Gamma"))
        ]
        for handle in handles[:3]:
            Message._base_manager.create(
                channel=channel,
                created_by=owner, sender=handle, status="synced", direction="inbound", sent_at=T0,
            )
        outbound = Message._base_manager.create(
            channel=channel, created_by=owner, status="sent", direction="outbound", sent_at=T0
        )
        for handle in handles:
            Participant._base_manager.create(created_by=owner, message=outbound, handle=handle, role="to")
    with actor_context(owner):
        for options, count, messages, labels in (
            (InboxNavigatorOptions(sort="name"), 2, 3, ["Alpha", "Beta"]),
            (InboxNavigatorOptions(sort="name", include_sent=True), 3, 4, ["Alpha", "Beta", "Gamma"]),
            (InboxNavigatorOptions(sort="name", lens="recipients"), 3, 1, ["Alpha", "Beta", "Gamma"]),
            (InboxNavigatorOptions(sort="name", lens="handles"), 3, 3, ["Alpha", "Beta", "Zulu"]),
        ):
            navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), options)
            result = navigator.page()
            assert (result.count, result.message_count) == (count, messages)
            assert [row.label for row in result.rows] == labels
            for page in (1, 2, 20):
                result = navigator.page(page=page, size=1)
                assert (result.count, result.message_count) == (count, messages)


def test_accounts_require_readable_account_and_include_channel_readable_messages():
    owner = User.objects.create_user(username="explorer-account-owner")
    other = User.objects.create_user(username="explorer-account-other")
    with system_context(reason="seed source account eligibility"):
        accounts = {
            name: make_integration(f"explorer-{name}", model=Channel, owner=owner, created_by=owner)
            for name in ("eligible", "draft", "public", "owned-channel-other-author", "empty")
        }
        hidden = make_integration("explorer-hidden", model=Channel, owner=other, created_by=other)
        public = Thread._base_manager.create(created_by=owner, modality="public_thread")
        for name, account in accounts.items():
            if name != "empty":
                Message._base_manager.create(
                    created_by=other if name == "owned-channel-other-author" else owner,
                    channel=account,
                    status="draft" if name == "draft" else "synced",
                    thread=public if name == "public" else None,
                )
        Message._base_manager.create(created_by=owner, channel=hidden, status="synced")
    with actor_context(owner):
        assert [account.pk for account in Message.objects.all().explorer().accounts()] == [
            accounts["eligible"].pk,
            accounts["owned-channel-other-author"].pk,
        ]


def test_recency_classifies_latest_identity_activity_and_keeps_older_messages(monkeypatch):
    owner = User.objects.create_user(username="explorer-recency")
    today = datetime(2026, 9, 11, 12, tzinfo=UTC)
    monkeypatch.setattr("angee.messaging.inbox_navigator.timezone.now", lambda: today)
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed recent and old activity"):
        handle = Handle._base_manager.create(created_by=owner, platform="email", value="recent@example.com")
        for instant in (T0, today):
            Message._base_manager.create(
                channel=channel,
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
    other_channel = make_integration(other.username, model=Channel, owner=other)
    channel = make_integration(owner.username, model=Channel, owner=owner)
    channels = {owner.pk: channel, other.pk: other_channel}
    with system_context(reason="seed explorer"):
        fragment = Fragment.objects.upsert(text="shared document text", created_by_id=owner.pk)
        rows = [
            Message._base_manager.create(
                channel=channels[user.pk], created_by=user, status=status, platform=platform, sent_at=T0
            )
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
        title = Fragment.objects.upsert(text="Selected shared title", created_by_id=owner.pk)
        Part._base_manager.create(created_by=owner, message=rows[0], role="title", fragment=title, position=2)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        related = inbox.related(f"fragment:{fragment.sqid}")
        assert related.count == related.message_count == 2
        assert {row.pk for row in related.rows} == {row.pk for row in rows[:2]}
        shared = InboxResults(inbox, inbox.messages, InboxResultOptions(lens="text"))
        for axis in ("platform", "conversation"):
            groups = shared.groups(axis).page(size=1)
            assert (groups.count, groups.record_count, groups.message_count) == (2, 1, 2)
        results = inbox.results(InboxCoverage(platforms=["email"]), InboxSearch(text='"shared document"'))
        assert list(results.values_list("pk", flat=True)) == [rows[0].pk]
        content = InboxResults(inbox, results, InboxResultOptions(lens="text"))
        page = content.page()
        assert page.count == page.message_count == 1
        assert page.rows[0].count == 1 and page.rows[0].part_count == 2
        assert page.rows[0].id == f"fragment:{fragment.sqid}"
        assert page.rows[0].message.title() == "Selected shared title"
        assert content.groups("platform").page().record_count == 1
        assert content.page(scope=InboxGroupScope(axis="platform", value="email")).rows[0].count == 1
        related_target = InboxRelated(inbox, f"fragment:{fragment.sqid}", str(rows[1].sqid))
        assert related_target.page(text="no matching text").count == 0
        assert related_target.summary().count == 2
        assert related_target.summary().source.pk == rows[1].pk
        with system_context(reason="remove the only other eligible shared-text use"):
            Message._base_manager.filter(pk=rows[1].pk).update(status="draft")
        # Repeated parts in one message, private uses and drafts cannot qualify
        # a fragment as shared once the other accessible message disappears.
        assert content.page().count == 0


def test_related_relations_authorize_both_endpoints_and_keep_reply_provenance():
    owner = User.objects.create_user(username="explorer-relations")
    other = User.objects.create_user(username="explorer-unreadable-endpoint")
    Edge = apps.get_model("messaging", "MessageEdge")
    other_channel = make_integration(other.username, model=Channel, owner=other)
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed message relations"):
        source = Message._base_manager.create(channel=channel, created_by=owner, status="synced", sent_at=T0)
        reply = Message._base_manager.create(
            channel=channel,
            created_by=owner, status="synced", parent=source, sent_at=T0 + timedelta(days=1)
        )
        hidden = Message._base_manager.create(channel=other_channel, created_by=other, status="synced", sent_at=T0)
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed explorer"):
        thread = Thread._base_manager.create(created_by=other)
        message = Message._base_manager.create(
            channel=channel, created_by=owner, thread=thread, status="synced", sent_at=T0
        )
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        result = inbox.conversations(inbox.messages)
        assert result.rows[0].thread is None
        assert result.rows[0].id == f"message:{message.sqid}"
        groups = InboxResults(inbox, inbox.messages, InboxResultOptions()).groups("conversation").page()
        assert groups.rows[0].value == f"message:{message.sqid}"


def test_live_schema_projects_sections_and_independent_message():
    owner = User.objects.create_user(username="explorer-schema")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed explorer"):
        handle = Handle._base_manager.create(created_by=owner, value="sender@example.com", platform="email")
        message = Message._base_manager.create(
            channel=channel,
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
        matches message { id starred }
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
    assert result_data(execute_schema(
        _schema(), "query($id: ID!) { inbox_message_uses(id: $id) { target count } }",
        {"id": str(message.sqid)}, request=request,
    ))["inbox_message_uses"] == []
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


@pytest.mark.django_db(transaction=True)
def test_selected_message_open_stays_flat_and_uses_remain_actor_scoped_as_corpus_grows():
    owner = User.objects.create_user(username="explorer-message-budget")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    document = """query NexusInboxMessage($id: ID!) {
      inbox_message(id: $id) { matches message { id } }
    }"""
    uses_document = """query($id: ID!) { inbox_message_uses(id: $id) { part_id target count } }"""
    with system_context(reason="seed selected use target"):
        fragment = Fragment.objects.upsert(text="Shared selected target", created_by_id=owner.pk)
        selected = Message._base_manager.create(channel=channel, created_by=owner, status="synced", sent_at=T0)
        Part._base_manager.create(message=selected, created_by=owner, fragment=fragment)
    for corpus_size in (1, 5, 10):
        with system_context(reason="grow message corpus"):
            for index in range(corpus_size - Message._base_manager.count()):
                message = Message._base_manager.create(
                    channel=channel, created_by=owner, status="synced", sent_at=T0 + timedelta(minutes=index + 1)
                )
                Part._base_manager.create(message=message, created_by=owner, fragment=fragment)
        with actor_context(owner):
            inbox = Message.objects.all().explorer()
            with CaptureQueriesContext(connection) as statements:
                message, matches = inbox.selected_message(str(selected.sqid), InboxCoverage(), InboxSearch())
            assert matches is True
            assert message.pk == selected.pk
            assert len(statements) == 3
            assert all(f'FROM "{Part._meta.db_table}"' not in statement["sql"] for statement in statements)
            # The matches statement tests the admitted message's own fields; it must not compile Message.read.
            assert "rebac_relationshipregistry" not in statements[-1]["sql"]
            with CaptureQueriesContext(connection) as uses_statements:
                uses = inbox.message_uses(str(selected.sqid))
            assert uses[0].count == corpus_size
            assert "rebac_relationshipregistry" in uses_statements[-1]["sql"]
            assert inbox.selected_message(
                str(selected.sqid), InboxCoverage(), InboxSearch(text="Shared selected target"),
            )[1]
            assert not inbox.selected_message(str(selected.sqid), InboxCoverage(), InboxSearch(attachment="any"))[1]
        with CaptureQueriesContext(connection) as document_statements:
            payload = result_data(execute_schema(
                _schema(), document, {"id": str(selected.sqid)}, request=request,
            ))["inbox_message"]
        assert payload["matches"] is True
        assert len(document_statements) == 6
        assert all(f'FROM "{Part._meta.db_table}"' not in statement["sql"] for statement in document_statements)
        uses_payload = result_data(execute_schema(
            _schema(), uses_document, {"id": str(selected.sqid)}, request=request,
        ))
        assert uses_payload["inbox_message_uses"][0]["count"] == corpus_size
    other = User.objects.create_user(username="explorer-message-budget-hidden")
    hidden_channel = make_integration(other.username, model=Channel, owner=other)
    with system_context(reason="seed unreadable use"):
        hidden = Message._base_manager.create(channel=hidden_channel, created_by=other, status="synced", sent_at=T0)
        Part._base_manager.create(message=hidden, created_by=other, fragment=fragment)
    with actor_context(owner):
        assert Message.objects.all().explorer().message_uses(str(selected.sqid))[0].count == 10


def test_selected_message_admits_before_exposing_matches_or_part_uses():
    owner = User.objects.create_user(username="explorer-selected-owner")
    outsider = User.objects.create_user(username="explorer-selected-outsider")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed unreadable selected message"):
        message = Message._base_manager.create(channel=channel, created_by=owner, status="synced", sent_at=T0)
        fragment = Fragment.objects.upsert(text="Hidden selected content", created_by_id=owner.pk)
        Part._base_manager.create(message=message, created_by=owner, fragment=fragment)
    with actor_context(outsider):
        with pytest.raises(ValueError, match="Message unavailable"):
            Message.objects.all().explorer().message_uses(str(message.sqid))


def test_selected_merged_copy_is_outside_results_when_its_survivor_is_present():
    owner = User.objects.create_user(username="explorer-merged-copy")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed selected copy and survivor"):
        survivor = Message._base_manager.create(channel=channel, created_by=owner, status="synced", sent_at=T0)
        copy = Message._base_manager.create(
            channel=channel, created_by=owner, status="synced", sent_at=T0 + timedelta(minutes=1),
            merged_into=survivor,
        )
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        assert inbox.selected_message(str(copy.sqid), InboxCoverage(), InboxSearch())[1] is False
        assert inbox.selected_message(
            str(copy.sqid), InboxCoverage(platforms=["slack"]), InboxSearch(),
        )[1] is False
        assert inbox.selected_message(
            str(copy.sqid), InboxCoverage(after=T0 + timedelta(seconds=30)), InboxSearch(),
        )[1] is True
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    selected = result_data(execute_schema(
        _schema(),
        """query($id: ID!) { inbox_message(id: $id) { message { merged_into { id } } } }""",
        {"id": str(copy.sqid)}, request=request,
    ))["inbox_message"]["message"]
    assert selected["merged_into"]["id"] == str(survivor.sqid)


def test_new_reply_names_its_parent_for_reader_invalidation():
    owner = User.objects.create_user(username="explorer-reply-invalidation")
    with system_context(reason="seed reply relation"):
        parent = Message._base_manager.create(created_by=owner, status="synced", sent_at=T0)
        reply = Message._base_manager.create(created_by=owner, parent=parent, status="synced", sent_at=T0)
    assert [(ref.model_label, ref.public_id) for ref in reply.change_related_records()] == [
        ("messaging.Message", str(parent.sqid)),
    ]


@pytest.mark.django_db(transaction=True)
def test_result_batch_preserves_each_group_page_and_statement_budget():
    owner = User.objects.create_user(username="explorer-batch-budget")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed result groups"):
        threads = [Thread._base_manager.create(created_by=owner) for _ in range(25)]
        for index, thread in enumerate(threads):
            message = Message._base_manager.create(
                channel=channel, created_by=owner, thread=thread, status="synced", sent_at=T0 + timedelta(minutes=index)
            )
            title = Fragment.objects.upsert(text=f"Inbox title {index}", created_by_id=owner.pk)
            Part._base_manager.create(message=message, role="title", fragment=title, created_by=owner)
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    query = """query($pages: [InboxResultPageInput!]!) {
      inbox_results_batch(pages: $pages) {
        key error result { count message_count rows { id message { id title } } }
      }
    }"""
    for group_count in (1, 5, 25):
        pages = [
            {"key": f"group-{index}", "scope": {"axis": "conversation", "value": f"thread:{thread.sqid}"},
             "page": 1, "size": 3}
            for index, thread in enumerate(threads[:group_count])
        ]
        with CaptureQueriesContext(connection) as statements:
            result = result_data(execute_schema(_schema(), query, {"pages": pages}, request=request))
        assert [page["key"] for page in result["inbox_results_batch"]] == [page["key"] for page in pages]
        assert all(page["result"]["count"] == page["result"]["message_count"] == 1
                   and len(page["result"]["rows"]) == 1 for page in result["inbox_results_batch"])
        assert [page["result"]["rows"][0]["message"]["title"] for page in result["inbox_results_batch"]] == [
            f"Inbox title {index}" for index in range(group_count)
        ]
        sizes = [len(statement["sql"].encode("utf-8")) for statement in statements]
        # Measured SQLite 1/5/25: 8/20/80 statements, 124911/597399/2959903 SQL bytes,
        # max 54863/54863/54864; PostgreSQL: 8/20/80, 154671/746199/3703903 bytes,
        # max 69735/69735/69736. Three policy-bearing statements per group remain.
        assert len(statements) == 5 + 3 * group_count
        base_limit, group_limit, largest_limit = (
            (25_000, 145_000, 70_000) if connection.vendor == "sqlite" else (30_000, 180_000, 85_000)
        )
        assert sum(sizes) <= base_limit + group_limit * group_count
        assert max(sizes) <= largest_limit
    single = result_data(execute_schema(
        _schema(),
        """query($scope: InboxGroupScopeInput!) {
          inbox_results(scope: $scope, page: 1, size: 3) {
            count message_count rows { id message { id title } }
          }
        }""",
        {"scope": pages[0]["scope"]}, request=request,
    ))
    assert result["inbox_results_batch"][0]["result"] == single["inbox_results"]
    page_query = """query($size: Int!) {
      inbox_results(size: $size) { count rows { message { title } } }
    }"""
    page_totals = []
    for size in (1, 5, 25):
        with CaptureQueriesContext(connection) as page_statements:
            whole = result_data(execute_schema(_schema(), page_query, {"size": size}, request=request))
        assert whole["inbox_results"]["count"] == 25
        assert [row["message"]["title"] for row in whole["inbox_results"]["rows"]] == [
            f"Inbox title {index}" for index in range(24, 24 - size, -1)
        ]
        page_sizes = [len(statement["sql"].encode("utf-8")) for statement in page_statements]
        # Measured SQLite 1/5/25 rows: 6/6/6 statements, 85358/85358/85359 bytes,
        # max 41165/41165/41166; PostgreSQL: 6/6/6, 107594/107594/107595,
        # max 52283/52283/52284. Title selection adds no per-row statement.
        assert len(page_statements) == 6
        page_totals.append(sum(page_sizes))
        total_limit, largest_limit = (
            (105_000, 52_000) if connection.vendor == "sqlite" else (132_000, 65_000)
        )
        assert sum(page_sizes) <= total_limit
        assert max(page_sizes) <= largest_limit
    assert max(page_totals) - min(page_totals) <= 2_000
    mixed = [dict(page, page=2 if index == 0 else 1) for index, page in enumerate(pages[:2])]
    paged = result_data(execute_schema(_schema(), query, {"pages": mixed}, request=request))["inbox_results_batch"]
    assert [(page["result"]["count"], len(page["result"]["rows"])) for page in paged] == [(1, 0), (1, 1)]


def test_result_batch_keeps_readable_pages_when_one_group_is_unavailable():
    owner = User.objects.create_user(username="explorer-batch-readable")
    other = User.objects.create_user(username="explorer-batch-private")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed readable and private result groups"):
        readable = Thread._base_manager.create(created_by=owner)
        private = Thread._base_manager.create(created_by=other)
        Message._base_manager.create(channel=channel, created_by=owner, thread=readable, status="synced", sent_at=T0)
    request = RequestFactory().post("/graphql/console/")
    request.user = owner
    pages = [
        {"key": key, "scope": {"axis": "conversation", "value": f"thread:{thread.sqid}"}, "page": 1, "size": 3}
        for key, thread in (("readable", readable), ("private", private))
    ]
    batch = result_data(execute_schema(
        _schema(),
        """query($pages: [InboxResultPageInput!]!) {
          inbox_results_batch(pages: $pages) { key error result { count rows { id } } }
        }""",
        {"pages": pages}, request=request,
    ))["inbox_results_batch"]
    assert batch[0]["key"] == "readable" and batch[0]["error"] is None
    assert batch[0]["result"]["count"] == 1
    assert batch[1] == {"key": "private", "error": "An unexpected error occurred.", "result": None}


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
    outsider_channel = make_integration(outsider.username, model=Channel, owner=outsider)
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
        hidden = Message._base_manager.create(
            channel=outsider_channel, created_by=outsider, status="synced", sent_at=T0
        )
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
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
                channel=channel,
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed organization memberships"):
        kind = Kind._base_manager.create(slug="explorer-member", name="Member")
        party = Party._base_manager.create(created_by=owner, display_name="Member")
        handle = Handle._base_manager.create(
            created_by=owner, platform="email", value="member@example.com", party=party, party_link_confirmed=True
        )
        Message._base_manager.create(
            channel=channel, created_by=owner, sender=handle, direction="inbound", status="synced", sent_at=T0
        )
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
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="seed covered group and direct activity"):
        handle = Handle._base_manager.create(created_by=owner, platform="email", value="group@example.com")
        group = Thread._base_manager.create(created_by=owner, modality="group")
        for thread in (group, None):
            Message._base_manager.create(
                channel=channel,
                created_by=owner, thread=thread, sender=handle, direction="inbound", status="synced", sent_at=T0
            )
    with actor_context(owner):
        navigator = InboxNavigator(Message.objects.all(), InboxCoverage(), InboxNavigatorOptions())
        groups = navigator.groups("group").page()
        assert (groups.count, groups.record_count, groups.message_count) == (1, 1, 2)
        assert groups.rows[0].message_count == 1
        assert navigator.page(scope=InboxGroupScope(axis="group", value=None)).count == 0


def test_status_feeds_are_explicit_coverage_and_never_named_after_unrelated_authors():
    owner = User.objects.create_user(username="status-owner")
    hidden = User.objects.create_user(username="status-hidden")
    account = make_integration(owner.username, model=Channel, owner=owner)
    private_account = make_integration(hidden.username, model=Channel, owner=hidden)
    with system_context(reason="seed shared status feed and actual conversation"):
        feed = Thread._base_manager.create(created_by=owner, channel=account, platform="whatsapp", modality="status")
        chat = Thread._base_manager.create(created_by=owner, channel=account, platform="whatsapp", modality="direct")
        hidden_feed = Thread._base_manager.create(
            created_by=hidden, channel=private_account, platform="whatsapp", modality="status")
        authors = [Handle._base_manager.create(platform="whatsapp", value=value, display_name=name, created_by=owner)
                   for value, name in [("+4917000001", "Ada"), ("+4917000002", "Bob")]]
        updates = [Message._base_manager.create(created_by=owner, channel=account, thread=feed,
                   platform="whatsapp", sender=author, status="synced", sent_at=T0) for author in authors]
        direct = Message._base_manager.create(created_by=owner, channel=account, thread=chat, platform="whatsapp",
                                              sender=authors[0], status="synced", sent_at=T0)
        Message._base_manager.create(created_by=hidden, channel=private_account, thread=hidden_feed,
                                     platform="whatsapp", status="synced", sent_at=T0)
    with actor_context(owner):
        inbox = Message.objects.all().explorer()
        assert set(inbox.coverage(InboxCoverage()).values_list("pk", flat=True)) == {direct.pk}
        for kinds in (["direct"], ["group"], ["other"]):
            assert not inbox.coverage(InboxCoverage(kinds=kinds)).filter(thread=feed).exists()
        updates_scope = inbox.coverage(InboxCoverage(kinds=["updates"], accounts=[account.sqid]))
        assert set(updates_scope.values_list("pk", flat=True)) == {row.pk for row in updates}
        assert Thread.objects.filter(pk=feed.pk).conversation_labels() == {feed.pk: "Status updates"}
        groups = InboxResults(inbox, updates_scope, InboxResultOptions()).groups("conversation").page()
        assert [(row.label, row.modality, row.account, row.count) for row in groups.rows] == [
            ("Status updates", "status", account.display_name, 2)]
        assert set(inbox.coverage(InboxCoverage(kinds=["direct", "updates"])).values_list("pk", flat=True)) == {
            direct.pk, *(row.pk for row in updates)}
        assert not inbox.coverage(InboxCoverage(kinds=["updates"], after=T0 + timedelta(days=1))).exists()
