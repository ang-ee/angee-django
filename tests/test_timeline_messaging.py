"""Live chatter and source attachments retain independent event identities."""

from __future__ import annotations

import pytest
from rebac import actor_context, system_context

from angee.identity import public_id_of
from angee.messaging.testing.models import Message, ThreadAttachment
from angee.messaging.timeline import CommentKind
from angee.projects.testing.models import Task
from angee.timeline.tasks import sync_targets
from angee.timeline.testing.models import Event
from tests.conftest import create_user
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 -- fixture
from tests.messaging_campaign import grant
from tests.test_messaging_source_threads import sourced as sourced  # noqa: F401 -- fixture
from tests.test_timeline import read_timeline

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.messaging.timeline.timeline_kinds"]


def test_comment_post_then_trash_then_restore(comment_record) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        message = record.message_post("A comment on the record")
    event = Event.objects.for_record(record).get(kind="messaging.comment")
    assert (event.target_model, event.target_pk) == ("messaging.Message", message.pk)
    assert event.target_id == public_id_of(message) and event.title == message.preview
    assert read_timeline(record, author)["timeline"]["entries"][0]["target_id"] == public_id_of(message)
    with actor_context(comment_record.writer):
        record.message_trash(message)
    assert not Event.objects.filter(pk=event.pk).exists()
    with actor_context(comment_record.writer):
        record.message_restore(message)
    assert Event.objects.for_record(record).get(kind="messaging.comment").target_pk == message.pk

    with system_context(reason="test.timeline.bulk_trash"):
        Message.objects.filter(pk=message.pk).trash()
    assert not Event.objects.for_record(record).exists()
    with system_context(reason="test.timeline.bulk_restore"):
        Message.objects.filter(pk=message.pk).restore()
    assert Event.objects.for_record(record).get(kind="messaging.comment").target_pk == message.pk


def test_binding_two_records_and_unbinding_one_preserves_the_other(sourced) -> None:
    owner, record, thread = sourced["owner"], sourced["task"], sourced["thread"]
    with actor_context(owner):
        other = Task.objects.create(title="Second source record")
        first = ThreadAttachment.objects.bind_source_thread(record, thread, label="First source")
        second = ThreadAttachment.objects.bind_source_thread(other, thread, label="Second source")
    assert Event.objects.for_record(record).get(kind="messaging.conversation").target_pk == first.pk
    kept = Event.objects.for_record(other).get(kind="messaging.conversation")
    assert kept.target_model == "messaging.ThreadAttachment" and kept.target_pk == second.pk

    bystander = create_user("timeline-conversation-bystander")
    grant(other, "reader", bystander)
    hidden = read_timeline(other, bystander)
    assert hidden["timeline"]["entries"] == []
    assert hidden["timeline"]["kinds"][-1]["count"] == 0
    assert hidden["timeline_attention"] == 0

    with actor_context(owner):
        ThreadAttachment.objects.unbind_source_thread(record, thread)
    assert not Event.objects.for_record(record).filter(kind="messaging.conversation").exists()
    assert Event.objects.for_record(other).get(kind="messaging.conversation").pk == kept.pk
    with actor_context(owner):
        ThreadAttachment.objects.filter(pk=second.pk).delete()
    assert not Event.objects.for_record(other).filter(kind="messaging.conversation").exists()


def test_thread_counters_and_source_attachments_do_not_reproject_comments(comment_record, monkeypatch) -> None:
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


def test_large_fanout_enqueues_bounded_tasks_then_projects_committed_targets(comment_record, monkeypatch) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        for index in range(3):
            record.message_post(f"Comment {index}")
    sent = []
    monkeypatch.setattr("angee.timeline.events.TARGET_BATCH_SIZE", 2)
    monkeypatch.setattr("angee.timeline.events.enqueue_task", lambda name, **kwargs: sent.append((name, kwargs)))
    thread = record.message_thread(create=False)
    assert thread is not None
    with system_context(reason="test.timeline.fanout"):
        thread.save(update_fields=["merged_into"])
    assert len(sent) == 2
    assert all(name == "timeline.sync_targets" and len(kwargs["kwargs"]["targets"]) <= 2 for name, kwargs in sent)
    for _, kwargs in sent:
        sync_targets(**kwargs["kwargs"])
    assert Event.objects.for_record(record).filter(kind="messaging.comment").count() == 3


def test_comments_obey_the_hosts_thread_read_permission(comment_record, monkeypatch) -> None:
    record, author = comment_record.record, comment_record.author
    with actor_context(author):
        record.message_post("Visible to thread readers")
    monkeypatch.setattr(type(record), "thread_read_access", "write")
    hidden = read_timeline(record, author)
    assert hidden["timeline"]["entries"] == []
    assert next(kind["count"] for kind in hidden["timeline"]["kinds"] if kind["id"] == "messaging.comment") == 0
    assert len(read_timeline(record, comment_record.writer)["timeline"]["entries"]) == 1
