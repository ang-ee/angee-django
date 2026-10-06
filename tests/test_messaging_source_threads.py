"""Source conversations attach to a record through the messaging schema's target arms."""

from typing import Any

import pytest
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    generic_target,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.messaging.testing.models import Thread, ThreadAttachment
from angee.projects.testing.models import Project, Task
from tests.chatterdemo.models import ChatterDoc, TrackedRecordParent
from tests.conftest import create_user, vault_for

pytestmark = pytest.mark.django_db(transaction=True)


def _grant(resource: Any, relation: str, user: Any) -> None:
    write_relationships([RelationshipTuple(to_object_ref(resource), relation, to_subject_ref(user))])


@pytest.fixture
def sourced(composed_permissions: None) -> dict[str, Any]:
    """A task owner, a task reader who also reads the conversation, and the conversation."""

    del composed_permissions
    owner, reader, outsider = (create_user(name) for name in ("source-owner", "source-reader", "source-outsider"))
    with actor_context(owner):
        project = Project.objects.create(title="Sourced")
        task = Task.objects.create(project=project, title="Captured")
        project.grant_record_access("reader", reader)
    with system_context(reason="test.source_threads.seed"):
        thread = Thread.objects.create()
    # A thread reads its record relations through its attachments, so writing a
    # source edge also takes write on the thread: the owner edits it, the others read.
    _grant(thread, "editor", owner)
    for user in (reader, outsider):
        _grant(thread, "reader", user)
    return {"owner": owner, "reader": reader, "outsider": outsider, "task": task, "thread": thread}


def test_source_edge_takes_write_on_the_record_and_lists_in_one_scoped_statement(sourced: dict[str, Any]) -> None:
    owner, reader, outsider = sourced["owner"], sourced["reader"], sourced["outsider"]
    task, thread = sourced["task"], sourced["thread"]
    assert thread.with_actor(reader).has_access("read") and not task.with_actor(reader).has_access("write")
    with actor_context(reader), pytest.raises(PermissionDenied):
        ThreadAttachment.objects.bind_source_thread(task, thread)
    with actor_context(owner):
        edge = ThreadAttachment.objects.bind_source_thread(task, thread, label="Original request")
        assert edge.actor() is not None and edge.role == "source"
        assert ThreadAttachment.objects.bind_source_thread(task, thread).pk == edge.pk
    # Neither the record's nor the conversation's writer: the reader cannot withdraw the evidence.
    with actor_context(reader), pytest.raises(PermissionDenied):
        ThreadAttachment.objects.unbind_source_thread(task, thread)
    _grant(thread, "editor", reader)
    with actor_context(reader), pytest.raises(PermissionDenied):
        # Thread write alone is not record write.
        ThreadAttachment.objects.bind_source_thread(task, Thread.objects.create())
    with actor_context(reader):
        assert [row.pk for row in ThreadAttachment.objects.source_threads_for_record(task)] == [edge.pk]
        key = generic_target(task).lookups(ThreadAttachment, "target")
        assert [row.pk for row in ThreadAttachment.objects.filter(role="source", **key)] == [edge.pk]
    with actor_context(outsider), pytest.raises(PermissionDenied):
        ThreadAttachment.objects.source_threads_for_record(Task._base_manager.get(pk=task.pk).with_actor(outsider))
    chatter = ThreadAttachment.objects.for_record(task)
    assert chatter is None or chatter.pk != edge.pk  # Source edges are not the record's chatter.
    with actor_context(owner):
        assert ThreadAttachment.objects.unbind_source_thread(task, thread) == 1
        assert ThreadAttachment.objects.unbind_source_thread(task, thread) == 0


def test_source_edge_refuses_undeclared_and_untyped_records(sourced: dict[str, Any]) -> None:
    owner, thread = sourced["owner"], sourced["thread"]
    vault = vault_for(owner, name="Not attachable")
    with system_context(reason="test.source_threads.untyped"):
        ungated = TrackedRecordParent.objects.create(title="Ungated host")
    with actor_context(owner):
        assert vault.has_access("write") and thread.has_access("read")
        with pytest.raises(PermissionDenied):
            ThreadAttachment.objects.bind_source_thread(vault, thread)
        with pytest.raises(ValueError, match="no resource type"):
            ThreadAttachment.objects.bind_source_thread(ungated, thread)
    with system_context(reason="test.source_threads.system"):
        # System capture still retains evidence on declared types; an ungated host
        # keeps its own chatter, keyed on its own content type.
        assert ThreadAttachment.objects.bind_source_thread(sourced["task"], thread).pk
        chatter = ungated.message_thread_attachment(create=True)
        assert (chatter.content_type.model_class(), chatter.object_id) == (TrackedRecordParent, ungated.pk)
        assert ThreadAttachment.objects.for_record(ungated).pk == chatter.pk
        assert not ThreadAttachment.objects.filter(**generic_target(vault).lookups(ThreadAttachment, "target")).exists()


def test_test_contributor_declares_the_chatter_demo_record(sourced: dict[str, Any]) -> None:
    thread = sourced["thread"]
    writer, reader = create_user("doc-writer"), create_user("doc-reader")
    with system_context(reason="test.source_threads.doc"):
        doc = ChatterDoc.objects.create(title="Gated")
    _grant(doc, "writer", writer)
    _grant(doc, "reader", reader)
    _grant(thread, "editor", writer)
    _grant(thread, "reader", reader)
    with actor_context(writer):
        edge = ThreadAttachment.objects.bind_source_thread(doc, thread)
    with actor_context(reader):
        assert [row.pk for row in ThreadAttachment.objects.source_threads_for_record(doc)] == [edge.pk]
        with pytest.raises(PermissionDenied):
            ThreadAttachment.objects.unbind_source_thread(doc, thread)
    with actor_context(writer):
        assert ThreadAttachment.objects.unbind_source_thread(doc, thread) == 1
