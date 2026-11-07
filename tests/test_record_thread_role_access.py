"""Reading a record does not disclose an attached source conversation."""

import pytest
from rebac import actor_context, system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.messaging.testing.models import Thread, ThreadAttachment
from angee.projects.testing.models import Project, Task
from tests.conftest import create_user
from tests.test_messaging_graphql import _schema

pytestmark = pytest.mark.django_db(transaction=True)


def test_record_reader_needs_source_thread_read(composed_permissions: None) -> None:
    owner = create_user("source-owner")
    reader = create_user("source-record-reader")
    with actor_context(owner):
        project = Project.objects.create(title="Sourced")
        task = Task.objects.create(project=project, title="Captured")
        project.grant_record_access("reader", reader)
    with system_context(reason="test.source_thread.seed"):
        thread = Thread.objects.create(owner=owner)
    with actor_context(owner):
        ThreadAttachment.objects.bind_source_thread(task, thread)
    assert task.with_actor(reader).has_access("read")
    assert not thread.with_actor(reader).has_access("read")

    query = """query($id: ID!) {
      record_thread(input: {model_label: "projects.Task", record_id: $id, role: "source"}) {
        error_code thread { id } message_result_count
      }
    }"""
    denied = result_data(execute_schema(_schema(), query, {"id": str(task.sqid)}, user=reader))["record_thread"]
    assert denied["error_code"] == "PERMISSION_DENIED"
    assert denied["thread"] is None
    mark_read = """mutation($id: ID!) {
      mark_record_thread_read(input: {model_label: "projects.Task", record_id: $id, role: "source"}) { error_code }
    }"""
    marked = result_data(execute_schema(_schema(), mark_read, {"id": str(task.sqid)}, user=reader))
    assert marked["mark_record_thread_read"]["error_code"] == "PERMISSION_DENIED"
    with actor_context(owner):
        allowed = result_data(execute_schema(_schema(), query, {"id": str(task.sqid)}, user=owner))["record_thread"]
    assert allowed["error_code"] is None
    assert allowed["thread"]["id"] == str(thread.sqid)
