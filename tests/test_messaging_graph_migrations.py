"""Preserve channel receipt cutoffs while upgrading old shared conversations."""

from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState
from django.db.models.functions import MD5

from angee.messaging.runtime_migrations import source_thread_channels
from tests.tables import model_tables
from tests.test_runtime_migrations import isolated_upgrade_database as isolated_upgrade_database


@pytest.fixture
def historical_source_threads(transactional_db, isolated_upgrade_database):
    state = ProjectState()

    def add(name, fields, **options):
        state.add_model(ModelState("messaging", name, [
            ("id", models.BigAutoField(primary_key=True)), *fields,
        ], options={"db_table": f"test_source_split_{name.lower()}", **options}))

    add("Channel", [])
    add("Thread", [
        ("channel", models.ForeignKey("messaging.Channel", models.SET_NULL, null=True)),
        ("owner_id", models.BigIntegerField(null=True)),
        ("platform", models.CharField(max_length=30)),
        ("external_id", models.TextField()),
        ("message_count", models.PositiveIntegerField(default=0)),
        ("last_message_at", models.DateTimeField(null=True)),
    ], constraints=[models.UniqueConstraint(
        models.F("platform"), MD5("external_id"), name=source_thread_channels.OLD,
    )])
    add("Message", [
        ("thread", models.ForeignKey("messaging.Thread", models.CASCADE)),
        ("channel", models.ForeignKey("messaging.Channel", models.CASCADE)),
        ("message_type", models.CharField(max_length=20, default="chat")),
        ("platform", models.CharField(max_length=30, default="email")),
        ("external_id", models.TextField(default="")),
        ("sent_at", models.DateTimeField(null=True)),
        ("created_at", models.DateTimeField()),
        ("is_trashed", models.BooleanField(default=False)),
    ])
    add("Participant", [
        ("thread", models.ForeignKey("messaging.Thread", models.CASCADE)),
        ("message", models.ForeignKey("messaging.Message", models.CASCADE, null=True)),
        ("handle_id", models.BigIntegerField()),
    ])
    add("ThreadNotification", [
        ("thread", models.ForeignKey("messaging.Thread", models.CASCADE)),
        ("message", models.ForeignKey("messaging.Message", models.CASCADE)),
        ("attachment", models.ForeignKey("messaging.ThreadAttachment", models.CASCADE, null=True)),
        ("follower", models.ForeignKey("messaging.ThreadFollower", models.SET_NULL, null=True)),
        ("read_at", models.DateTimeField(null=True)),
    ])
    add("ThreadAttachment", [
        ("thread", models.ForeignKey("messaging.Thread", models.CASCADE)),
        ("role", models.CharField(max_length=20)),
    ])
    add("ThreadFollower", [
        ("thread", models.ForeignKey("messaging.Thread", models.CASCADE)),
        ("attachment", models.ForeignKey("messaging.ThreadAttachment", models.SET_NULL, null=True)),
        ("party_id", models.BigIntegerField()),
        ("last_read_message", models.ForeignKey("messaging.Message", models.SET_NULL, null=True)),
        ("notification_policy", models.CharField(max_length=10)),
    ])
    with model_tables(tuple(state.apps.get_app_config("messaging").get_models())):
        yield state


def test_channel_split_rebases_original_and_copy_receipts_before_other_source_purge(historical_source_threads):
    state = historical_source_threads
    channels = state.apps.get_model("messaging", "Channel").objects
    threads = state.apps.get_model("messaging", "Thread").objects
    messages = state.apps.get_model("messaging", "Message").objects
    follows = state.apps.get_model("messaging", "ThreadFollower").objects
    attachments = state.apps.get_model("messaging", "ThreadAttachment").objects
    notifications = state.apps.get_model("messaging", "ThreadNotification").objects
    channel_a, channel_b = channels.create(), channels.create()
    thread = threads.create(channel=channel_a, platform="email", external_id="shared", message_count=3)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    before = messages.create(thread=thread, channel=channel_a, sent_at=start, created_at=start,
                             message_type="email", external_id="<shared@example.com>")
    anchor = messages.create(thread=thread, channel=channel_b, sent_at=start + timedelta(seconds=1), created_at=start,
                             message_type="email", external_id="<shared@example.com>")
    after = messages.create(thread=thread, channel=channel_a, sent_at=start + timedelta(seconds=2), created_at=start,
                            message_type="email", external_id="<later@example.com>")
    original_edge = attachments.create(thread=thread, role="source")
    original_follow = follows.create(thread=thread, party_id=42, last_read_message=anchor,
                                     attachment=original_edge, notification_policy="email")
    unread_follow = follows.create(thread=thread, party_id=43, notification_policy="muted")
    notice = notifications.create(thread=thread, message=anchor, attachment=original_edge,
                                  follower=original_follow, read_at=start)
    assert source_thread_channels.applies(state)
    with connection.schema_editor() as editor:
        upgraded = source_thread_channels.Migration("source_thread_channels", "messaging").apply(state.clone(), editor)
    assert not source_thread_channels.applies(upgraded)
    threads = upgraded.apps.get_model("messaging", "Thread").objects
    messages = upgraded.apps.get_model("messaging", "Message").objects
    other_thread = threads.get(channel=channel_b)
    # What one container showed as one conversation stays one: the copy redirects
    # to the original, and the email received through both accounts to its oldest row.
    assert other_thread.merged_into_id == thread.pk
    assert threads.get(pk=thread.pk).merged_into_id is None
    assert messages.get(pk=before.pk).thread_id == thread.pk
    assert messages.get(pk=after.pk).thread_id == thread.pk
    assert messages.get(pk=anchor.pk).thread_id == other_thread.pk
    assert messages.get(pk=anchor.pk).merged_into_id == before.pk
    assert messages.get(pk=before.pk).merged_into_id is None
    assert messages.get(pk=after.pk).merged_into_id is None
    assert follows.get(pk=original_follow.pk).last_read_message_id == before.pk
    copied_follow = follows.get(thread=other_thread, party_id=42)
    assert copied_follow.last_read_message_id == anchor.pk
    assert copied_follow.notification_policy == "email"
    copied_edge = attachments.get(thread=other_thread, role="source")
    assert copied_follow.attachment_id == copied_edge.pk
    retained_notice = notifications.get(pk=notice.pk)
    assert retained_notice.thread_id == other_thread.pk
    assert retained_notice.follower_id == copied_follow.pk
    assert retained_notice.attachment_id == copied_edge.pk
    assert retained_notice.read_at == start
    assert follows.get(pk=unread_follow.pk).last_read_message_id is None
    assert follows.get(thread=other_thread, party_id=43).last_read_message_id is None
    assert dict(threads.values_list("pk", "message_count")) == {thread.pk: 2, other_thread.pk: 1}
    messages.filter(thread=other_thread).delete()
    assert follows.get(pk=original_follow.pk).last_read_message_id == before.pk
    assert follows.get(pk=copied_follow.pk).last_read_message_id is None
