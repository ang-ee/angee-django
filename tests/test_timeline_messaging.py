"""Live chatter and source attachments retain independent event identities."""

from __future__ import annotations

import pytest
from django.apps import apps
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from angee.base.models.identity import public_id_of
from angee.base.signals import mute_changes
from angee.messaging.testing.models import Message, ThreadAttachment
from angee.messaging.timeline import CommentKind
from angee.projects.testing.models import Task
from angee.timeline.reading import Timeline
from tests.conftest import create_user
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 -- fixture
from tests.messaging_campaign import grant
from tests.test_messaging_source_threads import sourced as sourced  # noqa: F401 -- fixture
from tests.test_timeline import KINDS, read_timeline, timeline_entries

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.messaging.timeline.timeline_kinds"]


def test_comment_post_then_trash_then_restore(comment_record) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        message = record.message_post("A comment on the record")
    event, = timeline_entries(record, author, kind="messaging.comment")
    assert event.target_model == "messaging.Message"
    assert event.target_id == public_id_of(message) and event.title == message.preview
    assert read_timeline(record, author)["timeline"]["entries"][0]["target_id"] == public_id_of(message)
    with actor_context(comment_record.writer):
        record.message_trash(message)
    assert timeline_entries(record, author) == []
    with actor_context(comment_record.writer):
        record.message_restore(message)
    assert timeline_entries(record, author, kind="messaging.comment")[0].target_id == public_id_of(message)
    with system_context(reason="test.timeline.bulk_trash"):
        Message.objects.filter(pk=message.pk).trash()
    assert timeline_entries(record, author) == []
    with system_context(reason="test.timeline.bulk_restore"):
        Message.objects.filter(pk=message.pk).restore()
    assert timeline_entries(record, author, kind="messaging.comment")[0].target_id == public_id_of(message)


def test_comment_timeline_sender_label_is_implied_and_batches(
    comment_record, record_property, settings,
) -> None:
    """A record share admits each message's sender label without a sender grant."""

    record, owner, reader = comment_record.record, comment_record.author, comment_record.peer
    with system_context(reason="test.timeline.sender.seed"), mute_changes():
        attachment = ThreadAttachment.objects.ensure_for_record(record)
        handle = apps.get_model("parties", "Handle").objects.create(
            platform="email", value="private-timeline@example.test",
            display_name="Private Timeline Sender", created_by=owner,
        )
        for index in range(20):
            Message.objects.create(
                thread=attachment.thread, sender=handle, created_by=owner,
                message_type="comment", direction="internal", preview=f"Private comment {index}",
            )
    for actor, label in ((owner, handle.display_name), (reader, handle.display_name)):
        Timeline(record, actor).page(kinds=["messaging.comment"], limit=1)
        counts = []
        for size in (1, 5, 20):
            with CaptureQueriesContext(connection) as queries:
                entries = Timeline(record, actor).page(kinds=["messaging.comment"], limit=size).entries
            assert len(entries) == size
            assert {entry.actor for entry in entries} == {label}
            counts.append(len(queries))
        assert counts[0] == counts[1] == counts[2], counts
        assert counts[0] == 20, counts
        record_property(f"comment_timeline_{actor.username}_sql", counts)

    source, _ = CommentKind().source(record, reader)
    assert "_sender_name" not in source.query.annotations
    hydrated = list(CommentKind().queryset(record, reader).filter(sender_id=handle.pk))
    with CaptureQueriesContext(connection) as queries:
        labels = CommentKind().present(hydrated)
    assert len(labels) == 20 and {item.actor for item in labels.values()} == {handle.display_name}
    assert not queries

    settings.ANGEE_TIMELINE_KINDS = KINDS
    Timeline(record, owner).page(kinds=["messaging.comment"], limit=1)
    full_counts = []
    for size in (1, 5, 20):
        with CaptureQueriesContext(connection) as queries:
            entries = Timeline(record, owner).page(kinds=["messaging.comment"], limit=size).entries
        assert len(entries) == size
        full_counts.append(len(queries))
    assert full_counts == [38, 38, 38], full_counts
    record_property("full_comment_timeline_sql", full_counts)
    with CaptureQueriesContext(connection) as queries:
        assert Timeline(record, owner).page(kinds=["workflows.run"], limit=20).entries == []
    assert len(queries) == 32
    record_property("full_no_comment_timeline_sql", len(queries))


def test_comment_timeline_uses_implied_confirmed_party_name(comment_record) -> None:
    """A visible message carries its confirmed sender name without party read."""

    record, owner, reader = comment_record.record, comment_record.author, comment_record.peer
    with system_context(reason="test.timeline.linked_sender.seed"), mute_changes():
        attachment = ThreadAttachment.objects.ensure_for_record(record)
        party = apps.get_model("parties", "Party").objects.create(
            display_name="Timeline Party", created_by=owner,
        )
        handle = apps.get_model("parties", "Handle").objects.create(
            platform="email", value="timeline-linked@example.test",
            display_name="Timeline Handle", created_by=owner,
        )
        party.with_actor(owner).confirm(aspect="handle", handle=handle, values={})
        message = Message.objects.create(
            thread=attachment.thread, sender=handle, created_by=owner,
            message_type="comment", direction="internal", preview="Linked comment",
        )
    for actor in (owner, reader):
        hydrated = list(CommentKind().queryset(record, actor).filter(pk=message.pk))
        assert CommentKind().present(hydrated)[message.pk].actor == "Timeline Party"


def test_binding_two_records_and_unbinding_one_preserves_the_other(sourced) -> None:
    owner, record, thread = sourced["owner"], sourced["task"], sourced["thread"]
    with actor_context(owner):
        other = Task.objects.create(title="Second source record")
        first = ThreadAttachment.objects.bind_source_thread(record, thread, label="First source")
        second = ThreadAttachment.objects.bind_source_thread(other, thread, label="Second source")
    assert timeline_entries(record, owner)[0].target_id == public_id_of(first)
    kept, = timeline_entries(other, owner)
    assert kept.target_model == "messaging.ThreadAttachment" and kept.target_id == public_id_of(second)

    bystander = create_user("timeline-conversation-bystander")
    grant(other, "reader", bystander)
    hidden = read_timeline(other, bystander)
    assert hidden["timeline"]["entries"] == []
    assert hidden["timeline"]["kinds"][-1]["count"] == 0
    assert hidden["timeline_attention"] == 0

    with actor_context(owner):
        ThreadAttachment.objects.unbind_source_thread(record, thread)
    assert timeline_entries(record, owner) == []
    assert timeline_entries(other, owner)[0].key == kept.key
    with actor_context(owner):
        ThreadAttachment.objects.filter(pk=second.pk).delete()
    assert timeline_entries(other, owner) == []


def test_thread_counters_and_source_attachments_do_not_invalidate_comments(comment_record, monkeypatch) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        record.message_post("First comment")
    original = CommentKind.targets
    observed = []

    def targets(self, row):
        observed.append(row._meta.label)
        return original(self, row)

    monkeypatch.setattr(CommentKind, "targets", targets)
    with actor_context(author):
        record.message_post("Second comment")
    assert "messaging.Thread" not in observed
    assert list(CommentKind().targets(ThreadAttachment(role="source"))) == []


def test_comments_obey_the_hosts_thread_read_permission(comment_record, monkeypatch) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        record.message_post("Visible to thread readers")
    monkeypatch.setattr(type(record), "thread_read_access", "write")
    hidden = read_timeline(record, author)
    assert hidden["timeline"]["entries"] == []
    assert next(kind["count"] for kind in hidden["timeline"]["kinds"] if kind["id"] == "messaging.comment") == 0
    assert len(read_timeline(record, comment_record.writer)["timeline"]["entries"]) == 1
