"""A readable message or notification does not grant its parent thread's read."""
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from rebac import actor_context, system_context

from angee.messaging.testing.models import Channel, Fragment, Message, Thread
from tests import test_messaging as messaging_models
from tests.conftest import execute_schema, make_integration, result_data
from tests.test_messaging_graphql import _schema as messaging_schema
from tests.test_nexus import _schema as nexus_schema

pytestmark = pytest.mark.usefixtures("composed_tables")
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)

THREAD_FIELDS = (
    "id display_name conversation_label message_count last_message_at visibility platform "
    "title { text } messages { id } participants { id }"
)


def _seed(copy: bool = True):
    owner = User.objects.create_user(username="probe-thread-owner")
    outsider = User.objects.create_user(username="probe-outsider")
    outsider_channel = make_integration(outsider.username, model=Channel, owner=outsider)
    with system_context(reason="probe seed"):
        title = Fragment.objects.upsert(text="Board: confidential acquisition", created_by_id=owner.pk)
        thread = Thread._base_manager.create(created_by=owner, owner=owner, title=title, platform="email")
        private = [
            Message._base_manager.create(
                created_by=owner, thread=thread, status="synced", sent_at=T0 + timedelta(days=i)
            )
            for i in range(4)
        ]
        # One message of the private thread copied into the outsider's own channel.
        copied = None if not copy else Message._base_manager.create(
            created_by=owner, channel=outsider_channel, thread=thread, status="synced", sent_at=T0 + timedelta(days=9),
        )
        messaging_models.ThreadNotification._base_manager.create(thread=thread, message=private[0], user=outsider)
    return owner, outsider, thread, private, copied


def test_notification_recipient_cannot_read_parent_thread_display():
    _, outsider, thread, private, _ = _seed()
    with actor_context(outsider):
        assert not Thread.objects.with_actor(outsider).filter(pk=thread.pk).exists()
        assert not Message.objects.with_actor(outsider).filter(pk=private[0].pk).exists()
    query = "{ thread_notifications { id message { id } thread { %s } } }" % THREAD_FIELDS
    data = result_data(execute_schema(messaging_schema(), query, user=outsider))
    assert len(data["thread_notifications"]) == 1
    assert data["thread_notifications"][0]["message"] is None
    assert data["thread_notifications"][0]["thread"] is None


def test_channel_copy_reader_cannot_read_parent_thread_in_inbox_or_message_root():
    _, outsider, thread, _, copied = _seed()
    assert copied is not None
    with actor_context(outsider):
        assert not Thread.objects.with_actor(outsider).filter(pk=thread.pk).exists()
        copies = list(Message.objects.with_actor(outsider).filter(thread=thread).values_list("pk", flat=True))
        assert copies == [copied.pk]
    data = result_data(execute_schema(
        nexus_schema(), "query($id: ID!) { inbox_message(id: $id) { message { id thread { %s } } } }" % THREAD_FIELDS,
        {"id": str(copied.sqid)}, user=outsider,
    ))
    roots = result_data(execute_schema(
        messaging_schema(), "{ messages { id thread { %s } } }" % THREAD_FIELDS, user=outsider,
    ))
    assert data["inbox_message"]["message"]["thread"] is None
    assert roots["messages"] == [{"id": str(copied.sqid), "thread": None}]


def test_notification_alone_does_not_disclose_thread_counts_or_last_message():
    _, outsider, thread, private, _ = _seed(copy=False)
    with system_context(reason="probe counts"):
        Thread._base_manager.filter(pk=thread.pk).update(message_count=4, last_message_at=private[-1].sent_at)
    query = "{ thread_notifications { id message { id } thread { %s } } }" % THREAD_FIELDS
    data = result_data(execute_schema(messaging_schema(), query, user=outsider))
    assert len(data["thread_notifications"]) == 1
    assert data["thread_notifications"][0]["message"] is None
    assert data["thread_notifications"][0]["thread"] is None


def test_thread_reader_sees_parent_display_on_message_and_notification_roots():
    owner, _, thread, private, _ = _seed(copy=False)
    with system_context(reason="test readable notification"):
        messaging_models.ThreadNotification._base_manager.create(thread=thread, message=private[0], user=owner)
    with actor_context(owner):
        assert Thread.objects.with_actor(owner).filter(pk=thread.pk).exists()
    messages = result_data(execute_schema(
        messaging_schema(), "{ messages { id thread { id title { text } } } }", user=owner,
    ))["messages"]
    assert {row["id"] for row in messages} == {str(message.sqid) for message in private}
    assert all(row["thread"] == {
        "id": str(thread.sqid), "title": {"text": "Board: confidential acquisition"},
    } for row in messages)
    notifications = result_data(execute_schema(
        messaging_schema(), "{ thread_notifications { thread { id } } }", user=owner,
    ))["thread_notifications"]
    assert notifications == [{"thread": {"id": str(thread.sqid)}}]
