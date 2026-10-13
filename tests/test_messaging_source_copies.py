"""Source identity changes display without moving transport occurrences."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rebac import actor_context, system_context

from angee.messaging.testing.models import (
    Channel,
    Message,
    Thread,
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
)
from tests.conftest import make_integration
from tests.messaging_campaign import grant
from tests.test_messaging import ThreadedTicket, _ingest, _parsed
from tests.test_messaging import channel as channel

pytestmark = pytest.mark.django_db(transaction=True)


def test_hidden_older_source_copy_does_not_hide_visible_copy(channel):
    other = make_integration("copy-visible", model=Channel, backend_class="manual")
    _ingest([_parsed("shared@example.com")], channel=channel)
    _ingest([_parsed("shared@example.com")], channel=other)
    left, right = Message._base_manager.filter(external_id="shared@example.com").order_by("pk")
    assert left.thread_id != right.thread_id
    with actor_context(other.owner):
        assert list(Message.objects.inbox().representatives().values_list("pk", flat=True)) == [right.pk]
        assert list(Message.objects.inbox().source_copies(right).values_list("pk", flat=True)) == [right.pk]
    grant(channel, "reader", other.owner)
    with actor_context(other.owner):
        assert list(Message.objects.inbox().representatives().values_list("pk", flat=True)) == [left.pk]
        local = Message.objects.inbox().filter(channel=other).representatives()
        assert list(local.values_list("pk", flat=True)) == [right.pk]


def test_source_identity_decision_applies_and_dismisses_without_moving_rows(channel):
    other = make_integration("copy-decision", model=Channel, backend_class="manual", owner=channel.owner)
    _ingest([_parsed("decision@example.com")], channel=channel)
    _ingest([_parsed("decision@example.com")], channel=other)
    rows = list(Message._base_manager.filter(external_id="decision@example.com").order_by("pk"))
    original = [(row.pk, row.channel_id, row.thread_id) for row in rows]
    with actor_context(channel.owner):
        assert Message.objects.inbox().representatives().count() == 1
        rows[0].identify_source_copy(rows[1], same=False)
        assert Message.objects.inbox().representatives().count() == 2
        rows[0].identify_source_copy(rows[1], same=True)
        assert Message.objects.inbox().representatives().count() == 1
        assert Message.objects.inbox().for_feed(all_copies=True).count() == 2
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

        changed = kept.message_subscribe(user=watcher, notification_policy="muted", subtype_keys=())
        assert changed.pk == follow.pk
        assert changed.notification_policy == "muted"
        assert changed.subtype_keys == []
        assert changed.last_read_message_id == first.pk
        assert ThreadFollower.objects.for_thread(kept_thread).filter(party_id=follow.party_id).count() == 1


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
