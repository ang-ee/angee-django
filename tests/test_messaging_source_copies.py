"""Source identity folds channel copies into a retained redirect without moving rows."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory
from django.utils import timezone
from rebac import actor_context, system_context

from angee.messaging.inbox import InboxCoverage, InboxSearch
from angee.messaging.inbox_transcript import InboxTranscript
from angee.messaging.testing.models import (
    Channel,
    Message,
    Thread,
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
)
from tests.conftest import execute_schema, make_integration, result_data
from tests.messaging_campaign import grant
from tests.test_messaging import ThreadedTicket, _ingest, _parsed
from tests.test_messaging import channel as channel
from tests.test_nexus import _schema

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def retained_feed_copy(channel):
    other = make_integration("copy-anchor", model=Channel, backend_class="manual")
    _ingest([_parsed("anchor@example.com")], channel=channel)
    _ingest([_parsed("anchor@example.com")], channel=other)
    left, right = Message._base_manager.filter(external_id="anchor@example.com").order_by("pk")
    grant(channel, "reader", other.owner)
    return left, right, other.owner


def test_feed_keeps_the_exact_copy_across_windows_continuation_and_revalidation(retained_feed_copy):
    left, right, reader = retained_feed_copy
    third = make_integration("copy-anchor-unselected", model=Channel, backend_class="manual", owner=reader)
    _ingest([_parsed("anchor@example.com")], channel=third)
    unselected = Message._base_manager.get(channel=third, external_id="anchor@example.com")
    anchor = f"message:{right.public_id}"
    ids = [left.public_id, right.public_id]
    with actor_context(reader):
        query = Message.objects.inbox().untrashed().for_thread(right.thread)
        options = {"scope": ("thread", right.thread.public_id), "limit": 1}
        assert list(query.for_feed().values_list("pk", flat=True)) == [left.pk]
        page = query.feed_page(anchor=anchor, **options)
        assert [row.pk for row in page.rows] == [right.pk]
        assert page.count == 2
        older = query.feed_page(anchor=anchor, before_cursor=page.older_cursor, **options)
        assert [row.pk for row in older.rows] == [left.pk]
        newer = query.feed_page(anchor=anchor, after_cursor=older.newer_cursor, **options)
        assert [row.pk for row in newer.rows] == [right.pk]
        fixed = query.feed_page(anchor=anchor, through_cursor=page.older_cursor, **options)
        assert [row.pk for row in fixed.rows] == [right.pk]
        retained = query.feed_revalidate(ids, anchor=anchor)
        assert [row.pk for row in retained["messages"]] == [right.pk, left.pk]
        assert retained["absent_ids"] == []
        assert query.feed_revalidate([unselected.public_id], anchor=anchor)["absent_ids"] == [unselected.public_id]
        assert query.for_feed(all_copies=True).count() == 3
        assert query.feed_revalidate(ids)["absent_ids"] == [right.public_id]
        # The position remains a valid cut when the addressed occurrence changes.
        changed = query.feed_page(before_cursor=page.older_cursor, anchor=f"message:{left.public_id}", **options)
        assert [row.pk for row in changed.rows] == [left.pk]


@pytest.mark.parametrize("change", ["search", "move", "trash", "unreadable"])
def test_copy_anchor_cannot_widen_current_search_thread_or_read_scope(retained_feed_copy, channel, change):
    left, right, reader = retained_feed_copy
    anchor = f"message:{right.public_id}"
    ids = [right.public_id]
    with actor_context(reader):
        query = Message.objects.inbox().untrashed().for_thread(left.thread)
        options = {"scope": ("thread", left.thread.public_id), "anchor": anchor, "limit": 1}
        first = query.feed_page(**options)
    if change in ("move", "trash"):
        with system_context(reason="test retained copy scope changes"):
            if change == "move":
                outside = Thread._base_manager.create(channel=channel, platform="email")
                Message._base_manager.filter(pk=right.pk).update(thread=outside)
            else:
                Message._base_manager.filter(pk=right.pk).update(is_trashed=True)
    actor = channel.owner if change == "unreadable" else reader
    search = "does-not-match" if change == "search" else ""
    with actor_context(actor):
        query = Message.objects.inbox().untrashed().for_thread(left.thread)
        with pytest.raises(ValueError, match="Message unavailable in this conversation"):
            query.feed_page(search=search, **options)
        assert query.feed_revalidate(ids, search=search, anchor=anchor)["absent_ids"] == ids
        if change == "search":
            with pytest.raises(ValueError, match="Invalid message feed cursor"):
                query.feed_page(before_cursor=first.older_cursor, search=search, **options)
        elif change != "unreadable":
            # Cuts continue after the copy moves or disappears; their scope still binds search.
            continuation = query.feed_page(before_cursor=first.older_cursor, **options)
            assert [row.pk for row in continuation.rows] == [left.pk]


def test_copy_anchor_and_match_navigation_share_the_merged_transcript(retained_feed_copy):
    left, right, reader = retained_feed_copy
    request = RequestFactory().post("/graphql/console/")
    request.user = reader
    result = result_data(execute_schema(
        _schema(),
        """query($thread: ID!, $id: ID!, $anchor: String!) {
          thread_message_feed(thread_id: $thread, anchor: $anchor) { messages { id } count }
          thread_message_feed_revalidate(thread_id: $thread, ids: [$id], anchor: $anchor) {
            messages { id } absent_ids
          }
        }""",
        {"thread": left.thread.public_id, "id": right.public_id, "anchor": f"message:{right.public_id}"},
        request=request,
    ))
    assert [row["id"] for row in result["thread_message_feed"]["messages"]] == [right.public_id, left.public_id]
    assert result["thread_message_feed"]["count"] == 2
    assert result["thread_message_feed_revalidate"] == {"messages": [{"id": right.public_id}], "absent_ids": []}
    with actor_context(reader):
        inbox = Message.objects.all().explorer()
        matching = inbox.results(InboxCoverage(accounts=[right.channel.public_id]), InboxSearch())
        assert list(matching.values_list("pk", flat=True)) == [right.pk]
        transcript = InboxTranscript(inbox, matching, left.thread.public_id)
        result = transcript.matches(visible=[left.public_id, right.public_id], at=right.public_id)
        assert result.total == 2 and result.count == 1
        assert result.visible == [right.public_id]
        assert result.previous is None and result.next is None
        assert transcript.matches(visible=[], at=left.public_id).next == right.public_id


def test_email_copies_fold_into_the_oldest_and_a_reader_without_it_still_reads_their_own(channel):
    other = make_integration("copy-visible", model=Channel, backend_class="manual")
    _ingest([_parsed("shared@example.com")], channel=channel)
    _ingest([_parsed("shared@example.com")], channel=other)
    left, right = Message._base_manager.filter(external_id="shared@example.com").order_by("pk")
    assert left.thread_id != right.thread_id
    assert right.merged_into_id == left.pk and left.merged_into_id is None
    assert Thread._base_manager.get(pk=right.thread_id).merged_into_id == left.thread_id
    with actor_context(other.owner):
        # The survivor is out of reach, so the copy reads as the message.
        assert list(Message.objects.inbox().canonical().values_list("pk", flat=True)) == [right.pk]
    grant(channel, "reader", other.owner)
    with actor_context(other.owner):
        assert list(Message.objects.inbox().canonical().values_list("pk", flat=True)) == [left.pk]
        local = Message.objects.inbox().filter(channel=other).canonical()
        assert list(local.values_list("pk", flat=True)) == [right.pk]
        assert Message.objects.inbox().for_feed(all_copies=True).count() == 2


def test_a_person_keeps_copies_separate_and_confirms_them_again_without_moving_rows(channel):
    other = make_integration("copy-decision", model=Channel, backend_class="manual", owner=channel.owner)
    _ingest([_parsed("decision@example.com")], channel=channel)
    _ingest([_parsed("decision@example.com")], channel=other)
    rows = list(Message._base_manager.filter(external_id="decision@example.com").order_by("pk"))
    original = [(row.pk, row.channel_id, row.thread_id) for row in rows]
    with actor_context(channel.owner):
        assert Message.objects.inbox().canonical().count() == 1
        rows[0].identify_source_copy(rows[1], same=False)
        assert Message._base_manager.get(pk=rows[1].pk).merged_into_id is None
        assert Message.objects.inbox().canonical().count() == 2
        with system_context(reason="test"), pytest.raises(ValidationError, match="stay separate"):
            Message._base_manager.get(pk=rows[0].pk).fold([Message._base_manager.get(pk=rows[1].pk)])
        rows[0].identify_source_copy(rows[1], same=True)
        assert Message._base_manager.get(pk=rows[1].pk).merged_into_id == rows[0].pk
        assert Message.objects.inbox().canonical().count() == 1
    assert list(Message._base_manager.filter(pk__in=[row.pk for row in rows]).order_by("pk").values_list(
        "pk", "channel_id", "thread_id",
    )) == original


def test_retained_source_identity_blocks_destructive_channel_purge(channel):
    other = make_integration("copy-retained", model=Channel, backend_class="manual", owner=channel.owner)
    _ingest([_parsed("retained@example.com")], channel=channel)
    _ingest([_parsed("retained@example.com")], channel=other)
    with actor_context(channel.owner), pytest.raises(ProtectedError, match="Disconnect"):
        Channel.objects.purge(channel)
    assert Message._base_manager.filter(channel_id=channel.pk).exists()
    assert Message._base_manager.filter(channel_id=other.pk).exists()


def test_purging_a_survivor_without_evidence_releases_its_copy(channel):
    other = make_integration("copy-released", model=Channel, backend_class="manual", owner=channel.owner)
    _ingest([_parsed("released-a@example.com")], channel=channel)
    _ingest([_parsed("released-b@example.com")], channel=other)
    left = Message._base_manager.get(external_id="released-a@example.com")
    right = Message._base_manager.get(external_id="released-b@example.com")
    with system_context(reason="legacy redirect"):
        # A redirect the upgrade wrote carries no pair evidence; purging the
        # survivor's channel releases the copy instead of tripping over it.
        Message._base_manager.filter(pk=right.pk).update(merged_into=left)
        Thread._base_manager.filter(pk=right.thread_id).update(merged_into=left.thread_id)
    with actor_context(channel.owner):
        Channel.objects.purge(channel)
    assert not Message._base_manager.filter(pk=left.pk).exists()
    right.refresh_from_db()
    assert right.merged_into_id is None
    assert Thread._base_manager.get(pk=right.thread_id).merged_into_id is None


def test_chatter_fold_keeps_physical_messages_and_current_record_attachment(composed_tables):
    with system_context(reason="retained chatter fold"):
        kept = ThreadedTicket.objects.create(title="Kept")
        old = ThreadedTicket.objects.create(title="Old")
        original = old.message_post("An old comment")
        kept_thread = kept.message_thread()
        old_thread = old.message_thread()
        kept_thread.fold([old_thread])
        original.refresh_from_db()
        old_thread.refresh_from_db()
        assert original.thread_id == old_thread.pk
        assert old_thread.merged_into_id == kept_thread.pk
        assert Message.objects.for_thread(kept_thread).filter(pk=original.pk).exists()
        assert ThreadAttachment.objects.for_record(old).thread_id == kept_thread.pk
        assert not Thread.objects.inbox().filter(pk=old_thread.pk).exists()


def test_channel_thread_identity_accepts_both_native_foreign_key_forms(channel):
    with system_context(reason="channel occurrence identity"):
        first, created = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="native-channel-key", defaults={"channel": channel},
        )
        repeated, created_again = Thread.objects.get_or_create_by_external_id(
            platform="email", external_id="native-channel-key", defaults={"channel_id": channel.pk},
        )
    assert created
    assert not created_again
    assert first.pk == repeated.pk
    assert repeated.channel_id == channel.pk


def test_subscribe_reuses_effective_retained_follow_and_receipt(composed_tables):
    with system_context(reason="retained follow preferences"):
        watcher = get_user_model().objects.create_user(username="retained-follow-watcher")
        old = ThreadedTicket.objects.create(title="Original followed record")
        first = old.message_post("Already read")
        follow = old.message_subscribe(user=watcher, notification_policy="email", subtype_keys=("comment",))
        later = old.message_post("Still unread")
        old_thread = old.message_thread()
        kept = ThreadedTicket.objects.create(title="Current record")
        kept_thread = kept.message_thread()
        kept_thread.fold([old_thread])

        repeated = kept.message_subscribe(user=watcher)
        assert repeated.pk == follow.pk
        assert repeated.thread_id == old_thread.pk
        assert repeated.attachment_id == kept.message_thread_attachment().pk
        assert repeated.notification_policy == "email"
        assert repeated.subtype_keys == ["comment"]
        assert repeated.last_read_message_id == first.pk
        assert repeated.created_at == follow.created_at
        unread = set(ThreadFollower.objects.unread_messages(kept_thread, user=watcher).values_list("pk", flat=True))
        assert later.pk in unread
        assert first.pk not in unread
        messages = ThreadedTicket.thread_messages_expression(kept.pk)
        assert set(messages.unread_for(watcher).values_list("pk", flat=True)) == unread

        changed = kept.message_subscribe(user=watcher, notification_policy="muted", subtype_keys=())
        assert changed.pk == follow.pk
        assert changed.notification_policy == "muted"
        assert changed.subtype_keys == []
        assert changed.last_read_message_id == first.pk
        assert ThreadFollower.objects.for_thread(kept_thread).filter(party_id=follow.party_id).count() == 1
        assert not messages.unread_for(watcher).exists()


@pytest.mark.parametrize("bulk", [False, True])
@pytest.mark.parametrize("missing_survivor", [False, True])
def test_retired_host_deletion_retains_follow_and_notification_bookkeeping(
    composed_tables, bulk, missing_survivor,
):
    with system_context(reason="retired chatter host deletion"):
        watcher = get_user_model().objects.create_user(username="retained-deletion-watcher")
        old = ThreadedTicket.objects.create(title="Retired record")
        first = old.message_post("Already read before merge")
        follow = old.message_subscribe(user=watcher, subtype_keys=("comment",))
        later = old.message_post("Retained notification")
        notice = ThreadNotification._base_manager.get(message=later, user=watcher)
        notice.read_at = timezone.now()
        notice.save(update_fields=("read_at", "updated_at"))
        acknowledged_at = notice.read_at
        old_edge = old.message_thread_attachment()
        old_thread = old_edge.thread
        old_pk = old.pk
        kept = ThreadedTicket.objects.create(title="Surviving record")
        kept_edge = kept.message_thread_attachment()
        kept_edge.thread.fold([old_thread])
        if missing_survivor:
            ThreadAttachment._base_manager.filter(pk=kept_edge.pk).delete()

        def delete_old():
            if bulk:
                ThreadedTicket.objects.filter(pk=old_pk).delete()
            else:
                old.delete()

        if missing_survivor:
            with pytest.raises(ValidationError, match="surviving record attachment"):
                delete_old()
        else:
            delete_old()

        assert ThreadedTicket._base_manager.filter(pk=old_pk).exists() is missing_survivor
        assert ThreadAttachment._base_manager.filter(pk=old_edge.pk).exists() is missing_survivor
        expected_edge = old_edge if missing_survivor else kept_edge
        follow.refresh_from_db()
        notice.refresh_from_db()
        old_thread.refresh_from_db()
        assert follow.thread_id == old_thread.pk
        assert follow.attachment_id == expected_edge.pk
        assert follow.last_read_message_id == first.pk
        assert follow.notification_policy == "inbox"
        assert follow.subtype_keys == ["comment"]
        assert notice.thread_id == old_thread.pk
        assert notice.attachment_id == expected_edge.pk
        assert notice.follower_id == follow.pk
        assert notice.message_id == later.pk
        assert notice.read_at == acknowledged_at
        assert old_thread.merged_into_id == kept_edge.thread_id
        assert Message._base_manager.filter(pk__in=(first.pk, later.pk), thread_id=old_thread.pk).count() == 2
