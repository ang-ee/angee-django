"""Task lifecycle verbs keep the projection and live assignment predicate current."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from rebac import actor_context

from angee.graphql.publishing import mute_changes
from angee.projects.testing.models import Task
from angee.projects.timeline import TaskKind
from angee.timeline.testing.models import Event
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 - shared actor/record fixture
from tests.test_timeline import read_timeline

pytestmark = [
    pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("activity_catalog", "composed_permissions"),
]


@pytest.fixture(autouse=True)
def kinds(settings):
    settings.ANGEE_TIMELINE_KINDS = ["angee.projects.timeline.timeline_kinds"]


def test_task_assignment_and_lifecycle_drive_needs_me_and_delete_removes_the_line(comment_record) -> None:
    record, author, writer = comment_record.record, comment_record.author, comment_record.writer
    with actor_context(writer):
        task = Task.objects.schedule(record.with_actor(writer), title="Call the supplier", assignee=author)
    event = Event.objects.for_record(record).get()
    assert event.target_pk == task.pk
    data = read_timeline(record, author, needsMe=True)
    assert data["timeline_attention"] == data["timeline"]["needs_me"] == 1
    assert data["timeline"]["entries"][0]["needs_me"]
    assert read_timeline(record, writer, needsMe=True)["timeline"]["entries"] == []
    with actor_context(writer):
        task.with_actor(writer).complete()
    assert read_timeline(record, author)["timeline_attention"] == 0
    event.refresh_from_db()
    assert "Done" in event.detail
    with actor_context(writer):
        task.with_actor(writer).reopen()
    assert read_timeline(record, author)["timeline_attention"] == 1
    with actor_context(writer):
        task.with_actor(writer).drop("obsolete")
    assert read_timeline(record, author)["timeline_attention"] == 0
    with actor_context(writer):
        task.with_actor(writer).delete()
    assert not Event.objects.for_record(record).exists()


def test_backfill_purges_a_target_deleted_while_publication_was_muted(comment_record) -> None:
    record, writer = comment_record.record, comment_record.writer
    with actor_context(writer):
        task = Task.objects.schedule(record.with_actor(writer), title="Muted deletion")
        with mute_changes():
            task.with_actor(writer).delete()
    assert Event.objects.for_record(record).exists()
    call_command("timeline_backfill", kind=["projects.task"], verbosity=0)
    assert not Event.objects.for_record(record).exists()


def test_attention_uses_the_same_eligible_kinds_as_the_page(comment_record, monkeypatch) -> None:
    record, writer = comment_record.record, comment_record.writer
    with actor_context(writer):
        Task.objects.schedule(record.with_actor(writer), title="Waiting", assignee=writer)
    assert read_timeline(record, writer)["timeline_attention"] == 1
    monkeypatch.setattr(TaskKind, "eligible", lambda self, model: False)
    data = read_timeline(record, writer)
    assert data["timeline"]["entries"] == data["timeline"]["kinds"] == []
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0
