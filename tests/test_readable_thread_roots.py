"""A readable parent thread survives every message and notification read surface."""
from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from rebac import actor_context, system_context

from angee.iam.testing.graphql import measure_graphql_document
from angee.messaging.testing.models import Channel, Fragment, Message, Thread
from tests import test_messaging as messaging_models
from tests.conftest import execute_schema, make_integration
from tests.test_messaging_graphql import _schema as messaging_schema
from tests.test_nexus import _schema as nexus_schema

pytestmark = [pytest.mark.usefixtures("composed_tables"), pytest.mark.django_db(transaction=True)]
User = get_user_model()
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _seed():
    owner = User.objects.create_user(username="rv8-owner")
    channel = make_integration(owner.username, model=Channel, owner=owner)
    with system_context(reason="rv8 seed"):
        title = Fragment.objects.upsert(text="Readable conversation", created_by_id=owner.pk)
        thread = Thread._base_manager.create(created_by=owner, title=title, channel=channel)
        msgs = [
            Message._base_manager.create(
                created_by=owner, channel=channel, thread=thread, status="synced", direction="inbound",
                sent_at=T0 + timedelta(days=i),
            )
            for i in range(2)
        ]
        Message._base_manager.filter(pk=msgs[1].pk).update(parent=msgs[0])
        note = messaging_models.ThreadNotification._base_manager.create(thread=thread, message=msgs[0], user=owner)
    with actor_context(owner):
        assert Thread.objects.with_actor(owner).filter(pk=thread.pk).exists()
    return owner, thread, msgs, note


def _req(user):
    request = RequestFactory().post("/graphql/console/")
    request.user = user
    return request


def test_readable_thread_on_each_message_and_notification_root():
    owner, thread, msgs, note = _seed()
    tid = str(thread.sqid)
    out = {}
    nexus_q = {
        "inbox_message": (
            "query($id: ID!) { inbox_message(id: $id) { message { id thread { id } } } }",
            {"id": str(msgs[0].sqid)},
        ),
        "inbox_results_unscoped": ("{ inbox_results { rows { message { id thread { id } } } } }", {}),
        "inbox_results_thread_scope": (
            "query($s: InboxGroupScopeInput) { inbox_results(scope: $s) { rows { message { id thread { id } } } } }",
            {"s": {"axis": "conversation", "value": f"thread:{tid}"}},
        ),
        "inbox_results_day_scope": (
            "query($s: InboxGroupScopeInput) { inbox_results(scope: $s) { rows { message { id thread { id } } } } }",
            {"s": {"axis": "day", "value": "2026-01-01"}},
        ),
        "inbox_conversations": (
            "{ inbox_conversations { rows { id thread { id } messages { id thread { id } } } } }", {}
        ),
        "inbox_message_parent": (
            "query($id: ID!) { inbox_message(id: $id) { message { id parent { id thread { id } } } } }",
            {"id": str(msgs[1].sqid)},
        ),
        "batch_day_scope": (
            "query($p: [InboxResultPageInput!]!) { inbox_results_batch(pages: $p) "
            "{ key result { rows { message { id thread { id } } } } error } }",
            {"p": [{"key": "d", "scope": {"axis": "day", "value": "2026-01-01"}, "page": 1, "size": 3},
                   {"key": "t", "scope": {"axis": "conversation", "value": f"thread:{tid}"}, "page": 1, "size": 3}]},
        ),
        "starred_mutation": (
            "mutation($id: ID!) { set_inbox_message_starred(id: $id, starred: true) { id thread { id } } }",
            {"id": str(msgs[0].sqid)},
        ),
    }
    for name, (q, v) in nexus_q.items():
        res = execute_schema(nexus_schema(), q, v, request=_req(owner))
        out[name] = {"data": res.data, "errors": [str(e) for e in (res.errors or [])]}
    msg_q = {
        "messages": ("{ messages { id thread { id } } }", {}),
        "thread_notifications": ("{ thread_notifications { id thread { id } } }", {}),
        "messages_parent": ("{ messages { id parent { id thread { id } } } }", {}),
        "thread_messages_nested": ("{ threads { id messages { id thread { id } } } }", {}),
        "mark_read_mutation": (
            "mutation($id: ID!) { mark_thread_notification_read(id: $id) { id thread { id } } }",
            {"id": str(note.sqid) if hasattr(note, "sqid") else str(note.pk)},
        ),
    }
    for name, (q, v) in msg_q.items():
        res = execute_schema(messaging_schema(), q, v, user=owner)
        out[name] = {"data": res.data, "errors": [str(e) for e in (res.errors or [])]}
    expected = {
        "inbox_message": 1,
        "inbox_results_unscoped": 2,
        "inbox_results_thread_scope": 2,
        "inbox_results_day_scope": 1,
        "inbox_conversations": 3,
        "inbox_message_parent": 1,
        "batch_day_scope": 3,
        "starred_mutation": 1,
        "messages": 2,
        "thread_notifications": 1,
        "messages_parent": 1,
        "thread_messages_nested": 2,
        "mark_read_mutation": 1,
    }
    for surface, count in expected.items():
        assert not out[surface]["errors"], (surface, out[surface]["errors"])
        threads = _thread_values(out[surface]["data"])
        assert threads == [{"id": tid}] * count, surface
    assert out["inbox_message_parent"]["data"]["inbox_message"]["message"]["parent"]["id"] == str(msgs[0].sqid)
    assert [page["key"] for page in out["batch_day_scope"]["data"]["inbox_results_batch"]] == ["d", "t"]


def _thread_values(value):
    if isinstance(value, list):
        return [thread for item in value for thread in _thread_values(item)]
    if isinstance(value, dict):
        threads = [value["thread"]] if "thread" in value else []
        return threads + [thread for key, item in value.items() if key != "thread" for thread in _thread_values(item)]
    return []


def test_unscoped_inbox_results_thread_projection_has_one_flat_page_read():
    owner, thread, msgs, _ = _seed()
    channel = msgs[0].channel
    with system_context(reason="readable thread page budget"):
        for index in range(2, 25):
            Message._base_manager.create(
                created_by=owner, channel=channel, thread=thread, status="synced", direction="inbound",
                sent_at=T0 + timedelta(days=index),
            )
    query = "query($size: Int!) { inbox_results(size: $size) { rows { message { id thread { id } } } } }"
    schema = nexus_schema()
    reads = {size: measure_graphql_document(schema, query, {"size": size}, actor=owner) for size in (1, 5, 25)}
    for size, read in reads.items():
        assert len(read.data["inbox_results"]["rows"]) == size
        assert _thread_values(read.data) == [{"id": str(thread.sqid)}] * size
    reads[5].assert_flat_from(reads[1])
    reads[25].assert_flat_from(reads[5])
    assert [read.statement_count for read in reads.values()] == [7, 7, 7]
