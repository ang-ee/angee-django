"""A task read does not imply read on an attached source conversation."""

import pytest
from rebac import actor_context, system_context

from angee.messaging.testing.models import Message, Thread, ThreadAttachment
from angee.projects.testing.models import Project, Task
from tests.conftest import create_user, execute_schema, result_data
from tests.test_messaging_graphql import _schema

pytestmark = pytest.mark.django_db(transaction=True)


def test_task_reader_cannot_read_source_thread_messages(composed_permissions):
    owner, record_reader = create_user("source-owner"), create_user("source-reader")
    with actor_context(owner):
        project = Project.objects.create(title="Sourced")
        task = Task.objects.create(project=project, title="Captured")
        project.grant_record_access("reader", record_reader)
    with system_context(reason="test.source_thread.seed"):
        thread = Thread.objects.create(owner=owner)
        Message.objects.create(
            thread=thread, message_type="comment", direction="internal", preview="SECRET source body",
        )
    with actor_context(owner):
        ThreadAttachment.objects.bind_source_thread(task, thread, label="Original request")
    assert task.with_actor(record_reader).has_access("read")
    assert not thread.with_actor(record_reader).has_access("read")
    query = """query($id: ID!) { record_thread(input: {model_label: "projects.Task", record_id: $id, role: "source"}) {
         message_result_count messages { id preview } } }"""
    data = result_data(execute_schema(
        _schema(),
        query,
        {"id": str(task.sqid)}, user=record_reader,
    ))
    assert data["record_thread"]["message_result_count"] == 0, data
    owner_data = result_data(execute_schema(_schema(), query, {"id": str(task.sqid)}, user=owner))
    assert owner_data["record_thread"]["message_result_count"] == 1
    assert owner_data["record_thread"]["messages"][0]["preview"] == "SECRET source body"
