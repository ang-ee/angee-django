"""Record tasks: the task schema's record arms, the record's chatter, and the lifecycle."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.errors import RecordAccessSubjectRefused
from angee.messaging.testing.models import Message, Part, ThreadAttachment
from angee.projects.testing.models import Project, Queue, Task
from tests.chatterdemo.models import ChatterDoc
from tests.conftest import create_platform_admin

User = get_user_model()
pytestmark = [
    pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("composed_permissions", "activity_catalog"),
]


def _user(name: str) -> Any:
    with system_context(reason="test.record_tasks.user"):
        return User.objects.create_user(username=name, email=f"{name}@example.com")


def _grant(resource: Any, relation: str, user: Any) -> None:
    with system_context(reason="test.record_tasks.grant"):
        write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


def _doc(title: str = "Gated record") -> Any:
    with system_context(reason="test.record_tasks.doc"):
        return ChatterDoc.objects.create(title=title)


def _as(user: Any, row: Any) -> Any:
    """Re-read ``row`` under ``user``, as a request would."""

    with actor_context(user):
        return type(row)._default_manager.get(pk=row.pk)


def _posts() -> list[str]:
    messages = Message._base_manager.filter(subtype__key="activity_done").order_by("pk")
    return [Part._base_manager.select_related("fragment").get(message=message).fragment.text for message in messages]


def test_record_tasks_answer_to_their_records_readers_and_writers() -> None:
    writer, reader, stranger = _user("record-writer"), _user("record-reader"), _user("record-stranger")
    doc = _doc()
    _grant(doc, "writer", writer)
    _grant(doc, "reader", reader)

    with actor_context(writer):
        task = Task.objects.schedule(_as(writer, doc), title="Chase the supplier")
    assert task.assignee_id == writer.pk
    assert task.subject_object_id == doc.pk
    assert task.subject_content_type_id == ContentType.objects.get_for_model(doc).pk
    with actor_context(reader):
        assert list(Task.objects.for_record(_as(reader, doc))) == [task]
        with pytest.raises(PermissionDenied):
            Task.objects.schedule(_as(reader, doc), title="Not allowed")
        with pytest.raises(PermissionDenied):
            _as(reader, task).complete()
    with actor_context(stranger):
        assert list(Task.objects.for_record(doc)) == []
        assert not Task.objects.filter(pk=task.pk).exists()


def test_a_records_reader_reads_its_tasks_subject_pointer_without_loading_the_edge() -> None:
    owner, reader = _user("pointer-owner"), _user("pointer-reader")
    doc = _doc("Supplier memo")
    _grant(doc, "writer", owner)
    _grant(doc, "reader", reader)
    with actor_context(owner):
        task = Task.objects.schedule(_as(owner, doc), title="Answer the memo")
    with actor_context(reader):
        (row,) = Task.objects.for_record(_as(reader, doc))
        with CaptureQueriesContext(connection) as queries:
            reference = row.record_ref
    assert queries.captured_queries == []
    assert row.pk == task.pk
    assert (reference.model_label, reference.public_id) == ("chatterdemo.ChatterDoc", doc.sqid)


def test_a_record_tasks_own_grants_reach_nothing_past_its_record() -> None:
    """A direct grant, an assignment, ownership and a project reach a record task only with the record."""

    writer = _user("unbound-writer")
    grantee, assignee, owner, member = (_user(f"unbound-{name}") for name in ("grantee", "assignee", "owner", "member"))
    doc = _doc()
    _grant(doc, "writer", writer)
    with system_context(reason="test.record_tasks.unbound"):
        task = Task.objects.schedule(doc, title="Kept on the record", assignee=assignee)
        project = Project.objects.create(title="Elsewhere")
        Task._base_manager.filter(pk=task.pk).update(owner=owner, project=project)
    _grant(task, "reader", grantee)
    _grant(project, "reader", member)

    for user in (grantee, assignee, owner, member):
        with actor_context(user):
            assert not Task.objects.filter(pk=task.pk).exists(), user
        assert not Task._base_manager.get(pk=task.pk).with_actor(user).has_access("read"), user
    assert not Task._base_manager.get(pk=task.pk).with_actor(owner).has_access("transfer")
    with actor_context(writer):
        assert Task.objects.filter(pk=task.pk).exists()
    for permission in ("write", "transfer", "widen"):
        assert Task._base_manager.get(pk=task.pk).with_actor(writer).has_access(permission), permission


def test_a_task_takes_record_tasks_under_its_own_grants() -> None:
    owner, watcher, stranger = _user("subject-owner"), _user("subject-watcher"), _user("subject-stranger")
    with actor_context(owner):
        concern = Task.objects.create(title="Supplier concern")
    _grant(concern, "reader", watcher)

    with actor_context(owner):
        follow_up = Task.objects.schedule(_as(owner, concern), title="Call the supplier")
    with actor_context(watcher):
        assert list(Task.objects.for_record(_as(watcher, concern))) == [follow_up]
        with pytest.raises(PermissionDenied):
            _as(watcher, follow_up).complete()
    with actor_context(stranger):
        assert not Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(owner):
        with pytest.raises(ValidationError, match="takes no record tasks of its own"):
            Task.objects.schedule(_as(owner, follow_up), title="Nested")
        _as(owner, follow_up).complete()
    assert _posts() == ["Done: Call the supplier"]


def test_trash_withholds_record_tasks_from_readers_but_keeps_removal_authority() -> None:
    """The subject's trash policy governs its tasks; deleters can inspect and restore."""

    owner, reader = _user("concealed-owner"), _user("concealed-reader")
    with system_context(reason="test.record_tasks.concealed"):
        queue = Queue.objects.create(name="Concerns", key="CN", owner=owner)
        concern = Task.objects.create(title="Concealed concern", queue=queue, owner=owner)
        follow_up = Task.objects.schedule(concern, title="Call about it", assignee=owner)
    _grant(concern, "reader", reader)
    with actor_context(reader):
        assert Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(owner):
        concern.with_actor(owner).trash(reason="Removed")
        assert Task.objects.filter(pk=follow_up.pk).exists()
    with actor_context(reader):
        assert not Task.objects.filter(pk=follow_up.pk).exists()
        with pytest.raises(PermissionDenied):
            Task.objects.schedule(concern.with_actor(reader), title="After concealing")


@pytest.mark.parametrize("bulk", [False, True])
def test_a_deleted_record_takes_its_tasks_with_it(bulk: bool) -> None:
    """The task edge's target-deletion policy tears it down with its record."""

    writer = _user("record-deleter")
    doc, other = _doc("Doomed record"), _doc("Surviving record")
    _grant(doc, "manager", writer)
    _grant(other, "manager", writer)
    with actor_context(writer):
        task = Task.objects.schedule(_as(writer, doc), title="Goes with the record")
        kept = Task.objects.schedule(_as(writer, other), title="Stays with its record")

    with actor_context(writer):
        if bulk:
            ChatterDoc.objects.as_user(writer).filter(pk=doc.pk).delete()
        else:
            _as(writer, doc).delete()

    assert not ChatterDoc._base_manager.filter(pk=doc.pk).exists()
    assert not Task._base_manager.filter(pk=task.pk).exists()
    assert Task.history.filter(id=task.pk, history_type="-").count() == 1
    assert Task._base_manager.filter(pk=kept.pk).exists()


def test_a_record_task_is_assigned_only_to_someone_who_reads_the_record() -> None:
    writer, outsider = _user("record-assigner"), _user("record-outsider")
    doc = _doc()
    _grant(doc, "writer", writer)

    with actor_context(writer), pytest.raises(RecordAccessSubjectRefused):
        Task.objects.schedule(_as(writer, doc), title="Hand it over", assignee=outsider)
    assert not Task.objects.system_context(reason="test.record_tasks.none").exists()


def test_a_record_writer_completes_and_drops_tasks_they_neither_own_nor_hold() -> None:
    admin = create_platform_admin("record-admin")
    writer = _user("record-closer")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(admin):
        first = Task.objects.schedule(_as(admin, doc), title="Confirm the bank change", activity_type="call")
        second = Task.objects.schedule(_as(admin, doc), title="Obsolete follow-up")
    assert first.assignee_id == admin.pk

    with actor_context(writer):
        _as(writer, first).complete(feedback="Confirmed by phone.")
        _as(writer, second).drop("obsolete")

    first.refresh_from_db()
    second.refresh_from_db()
    assert (first.status, second.status, second.dropped_reason) == ("done", "dropped", "obsolete")
    (message,) = Message._base_manager.filter(subtype__key="activity_done")
    assert message.created_by_id == writer.pk
    assert _posts() == ["Call done: Confirm the bank change\n\nConfirmed by phone."]


def test_a_record_task_completes_once_and_closes_only_while_open() -> None:
    writer = _user("record-finisher")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(writer):
        record = _as(writer, doc)
        sent = Task.objects.schedule(record, title="Send the contract")
        stale = Task.objects.schedule(record, title="Old reminder")
        concurrent = _as(writer, sent)
        _as(writer, sent).complete(feedback="Sent.")
        concurrent.complete(feedback="Sent twice.")
        _as(writer, stale).drop("obsolete")
        with pytest.raises(ValidationError, match="Reopen this record task first"):
            _as(writer, stale).complete()
        with pytest.raises(ValidationError, match="Reopen this record task first"):
            _as(writer, sent).drop("obsolete")
        _as(writer, sent).reopen()
        _as(writer, sent).complete()

    assert _posts() == ["Done: Send the contract\n\nSent.", "Done: Send the contract"]


def test_a_logged_exchange_is_a_done_task_posted_on_the_record_with_no_chatter_of_its_own() -> None:
    author = _user("record-logger")
    doc = _doc("Logged case")
    _grant(doc, "writer", author)

    with actor_context(author):
        task = Task.objects.log(
            _as(author, doc), activity_type="call", occurred_on=date(2026, 9, 1),
            note="Walked through the export.\nAll good.",
        )

    task.refresh_from_db()
    assert (task.status, task.title, task.due_date) == ("done", "Walked through the export.", date(2026, 9, 1))
    assert (task.assignee_id, task.activity_type_id, task.note) == (
        author.pk, "call", "Walked through the export.\nAll good.",
    )
    assert _posts() == ["Call done: Walked through the export.\n\nAll good."]
    own = ContentType.objects.get_for_model(Task)
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()
    with actor_context(author):
        _as(author, task).complete()
        _as(author, task).reopen()
    assert len(_posts()) == 1
    assert not ThreadAttachment._base_manager.filter(content_type=own, object_id=task.pk).exists()


def test_a_records_tasks_list_open_first_then_by_due_date_and_title() -> None:
    writer = _user("record-planner")
    doc = _doc()
    _grant(doc, "writer", writer)
    with actor_context(writer):
        record = _as(writer, doc)
        Task.objects.schedule(record, title="Later", due_date=date(2026, 5, 1))
        Task.objects.schedule(record, title="Sooner", due_date=date(2026, 4, 1))
        Task.objects.schedule(record, title="Also sooner", due_date=date(2026, 4, 1))
        Task.objects.schedule(record, title="Finished", due_date=date(2026, 1, 1)).complete()
        everything = [task.title for task in Task.objects.for_record(record)]
        planned = [task.title for task in Task.objects.for_record(record, include_done=False)]

    assert everything == ["Also sooner", "Sooner", "Later", "Finished"]
    assert planned == ["Also sooner", "Sooner", "Later"]
