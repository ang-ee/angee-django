"""Task lifecycle verbs remain visible through the live union and assignment predicate."""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import actor_context

from angee.graphql.publishing import mute_changes
from angee.projects.testing.models import ActivityType, Task
from angee.projects.timeline import TaskKind
from tests.messaging_campaign import comment_record as comment_record  # noqa: F401 - shared actor/record fixture
from tests.test_timeline import read_timeline, timeline_entries

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
    event, = timeline_entries(record, author)
    assert event.title == task.title
    data = read_timeline(record, author, needsMe=True)
    assert data["timeline_attention"] == data["timeline"]["needs_me"] == 1
    assert data["timeline"]["entries"][0]["needs_me"]
    assert read_timeline(record, writer, needsMe=True)["timeline"]["entries"] == []
    with actor_context(writer):
        task.with_actor(writer).complete()
    assert read_timeline(record, author)["timeline_attention"] == 0
    event, = timeline_entries(record, author)
    assert "Done" in event.detail
    with actor_context(writer):
        task.with_actor(writer).reopen()
    assert read_timeline(record, author)["timeline_attention"] == 1
    with actor_context(writer):
        task.with_actor(writer).drop("obsolete")
    assert read_timeline(record, author)["timeline_attention"] == 0
    with actor_context(writer):
        task.with_actor(writer).delete()
    assert timeline_entries(record, writer) == []


def test_muted_deletion_needs_no_repair(comment_record) -> None:
    record, writer = comment_record.record, comment_record.writer
    with actor_context(writer):
        task = Task.objects.schedule(record.with_actor(writer), title="Muted deletion")
        with mute_changes():
            task.with_actor(writer).delete()
    assert timeline_entries(record, writer) == []


def test_attention_uses_the_same_eligible_kinds_as_the_page(comment_record, monkeypatch) -> None:
    record, writer = comment_record.record, comment_record.writer
    with actor_context(writer):
        Task.objects.schedule(record.with_actor(writer), title="Waiting", assignee=writer)
    assert read_timeline(record, writer)["timeline_attention"] == 1
    monkeypatch.setattr(TaskKind, "eligible", lambda self, model: False)
    data = read_timeline(record, writer)
    assert data["timeline"]["entries"] == data["timeline"]["kinds"] == []
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0


def test_record_without_task_capability_has_an_empty_task_timeline(comment_record) -> None:
    record = ActivityType.system_queryset().get(key="todo")
    assert not Task.declares_target(type(record))
    data = read_timeline(record, comment_record.writer)
    assert data["timeline"]["entries"] == data["timeline"]["kinds"] == []
    assert data["timeline"]["needs_me"] == data["timeline_attention"] == 0


def test_presentation_hydrates_native_task_rows_for_the_explicit_actor(comment_record, monkeypatch) -> None:
    record, author, writer = comment_record.record, comment_record.author, comment_record.writer
    with actor_context(writer):
        task = Task.objects.schedule(record.with_actor(writer), title="Review this exact task", assignee=author)
    native_queryset, native_present = TaskKind.queryset, TaskKind.present

    def queryset(kind, target, actor):
        assert target is record and actor is author
        return native_queryset(kind, target, actor)

    def present(kind, rows):
        rows = list(rows)
        assert [row.pk for row in rows] == [task.pk]
        assert all(not any(name.startswith("_timeline_") for name in vars(row)) for row in rows)
        with CaptureQueriesContext(connection) as relation_reads:
            result = native_present(kind, rows)
        assert list(relation_reads) == []
        return result

    monkeypatch.setattr(TaskKind, "queryset", queryset)
    monkeypatch.setattr(TaskKind, "present", present)
    with actor_context(comment_record.outsider):
        entry, = timeline_entries(record, author)
    assert entry.title == task.title and entry.actor == author.get_username()
    assert entry.needs_me
