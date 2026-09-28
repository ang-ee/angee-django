"""Recipient SQL scope, guarded relations, counts, and destructive graph edges."""

from unittest.mock import patch

import pytest
from django.contrib.contenttypes.models import ContentType
from rebac import RelationshipTuple, actor_context, delete_relationship, system_context, to_object_ref, to_subject_ref

from tests.chatterdemo.models import ChatterDoc
from tests.conftest import File as StorageFile
from tests.conftest import Vendor, create_platform_admin, execute_schema, result_data
from tests.messaging_campaign import grant, make_user
from tests.messaging_models import (
    Channel,
    Fragment,
    Handle,
    Message,
    Part,
    Party,
    Thread,
    ThreadAttachment,
    ThreadFollower,
    ThreadNotification,
)
from tests.test_messaging import MessageEdge, MessageStar, Participant, Reaction, ThreadActivity
from tests.test_messaging_graphql import _schema, _storage_drive, messaging_schema


@pytest.fixture(scope="module")
def campaign_schema():
    """Reuse the messaging suite's merged console composition."""
    return _schema()


def test_message_only_reader_gets_null_for_every_private_to_one_relation(composed_tables, campaign_schema, tmp_path):
    viewer, owner = make_user("message-viewer"), make_user("private-owner")
    with system_context(reason="test.messaging.graphql.relations"):
        vendor = Vendor.objects.create(slug="private-relations", display_name="Private")
        channel = Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual")
        thread = Thread.objects.create(owner=owner)
        parent = Message.objects.create(thread=thread, channel=channel)
        sender = Handle.objects.create(platform="email", value="hidden@example.com", created_by=owner)
        message = Message.objects.create(thread=thread, channel=channel, parent=parent, sender=sender)
        Participant.objects.create(message=message, thread=thread, handle=sender)
        hidden_parent = Part.objects.create(message=parent, position=0)
        _storage_drive(tmp_path, owner=owner)
        private_file = StorageFile.objects.ingest_bytes(b"Private attachment", filename="private.txt")
        Part.objects.create(message=message, parent=hidden_parent, position=0, file=private_file)
    grant(message, "reader", viewer)
    result = result_data(execute_schema(campaign_schema, """
        query Read($id: String!) {
          messages_by_pk(id: $id) {
            id thread { id } channel { id } parent { id } sender { id }
            participants { handle { id } }
          }
          parts(where: {message: {_eq: $id}}) { parent { id } file { id } }
        }
    """, {"id": message.sqid}, user=viewer))
    assert result["messages_by_pk"] == {
        "id": message.sqid, "thread": None, "channel": None, "parent": None, "sender": None,
        "participants": [{"handle": None}],
    }
    assert result["parts"] == [{"parent": None, "file": None}]


def test_fragment_counts_include_only_readable_messages_and_parts(composed_tables, campaign_schema):
    viewer = make_user("fragment-viewer")
    with system_context(reason="test.messaging.graphql.fragment"):
        shared = Fragment.objects.upsert(text="Shared content")
        visible, hidden = Message.objects.create(), Message.objects.create()
        for message in (visible, hidden):
            Part.objects.create(message=message, fragment=shared, position=0)
    grant(visible, "reader", viewer)
    result = result_data(execute_schema(campaign_schema, """
        query { parts { fragment { part_count message_count } } }
    """, user=viewer))
    assert result["parts"] == [{"fragment": {"part_count": 1, "message_count": 1}}]


@pytest.mark.parametrize("endpoint", [0, 1])
def test_generic_message_delete_removes_cross_channel_quote_for_either_endpoint_owner(
    composed_tables, campaign_schema, endpoint,
):
    owners = [make_user(f"delete-endpoint-{index}") for index in range(2)]
    with system_context(reason="test.messaging.graphql.edge-delete"):
        vendor = Vendor.objects.create(slug="quote-delete", display_name="Quote delete")
        channels = [Channel.objects.create(vendor=vendor, owner=owner, backend_class="manual") for owner in owners]
        messages = [Message.objects.create(channel=channel) for channel in channels]
        edge = MessageEdge.objects.create(src=messages[0], dst=messages[1], kind="quote")
    result = result_data(execute_schema(campaign_schema, """
        mutation Delete($id: String!) { delete_messages_by_pk(id: $id) { id } }
    """, {"id": messages[endpoint].sqid}, user=owners[endpoint]))
    assert result["delete_messages_by_pk"] == {"id": messages[endpoint].sqid}
    assert not MessageEdge._base_manager.filter(pk=edge.pk).exists()
    assert not Message._base_manager.filter(pk=messages[endpoint].pk).exists()
    assert Message._base_manager.filter(pk=messages[1 - endpoint].pk).exists()


@pytest.mark.parametrize("size", [1, 5, 10])
def test_inbox_scopes_recipients_in_sql_and_batches_record_pointers(
    composed_tables, campaign_schema, django_assert_max_num_queries, size,
):
    recipient, other, reader = (make_user(name) for name in ("inbox-recipient", "other-recipient", "record-reader"))
    with system_context(reason="test.messaging.graphql.inbox"):
        record = ChatterDoc.objects.create(title="Inbox pointer")
        attachment = ThreadAttachment.objects.ensure_for_record(record)
        notices = []
        for _ in range(size):
            message = Message.objects.create(thread=attachment.thread)
            notices.append(ThreadNotification.objects.create(message=message, thread=attachment.thread,
                                                             attachment=attachment, user=recipient))
            ThreadNotification.objects.create(message=message, thread=attachment.thread,
                                              attachment=attachment, user=other)
    grant(record, "reader", recipient)
    grant(record, "reader", reader)
    query = """query {
        thread_notifications { id read_at message { id attachment { record_id model_label } } }
    }"""
    result_data(execute_schema(campaign_schema, query, user=recipient))
    with patch.object(ChatterDoc, "thread_reader_allowed", autospec=True,
                      side_effect=ChatterDoc.thread_reader_allowed) as check:
        # Review handover's warm whole-request budget: 26 at N=1, 5 and 10.
        with django_assert_max_num_queries(26):
            data = result_data(execute_schema(campaign_schema, query, user=recipient))
    assert {row["id"] for row in data["thread_notifications"]} == {row.sqid for row in notices}
    assert all(row["read_at"] is None for row in data["thread_notifications"])
    assert all(row["message"]["attachment"]["record_id"] == record.sqid for row in data["thread_notifications"])
    assert check.call_count == 1
    for actor in (reader, create_platform_admin("inbox-admin")):
        assert result_data(execute_schema(campaign_schema, query, user=actor))["thread_notifications"] == []
    with system_context(reason="test.messaging.graphql.revoke"):
        delete_relationship(
            RelationshipTuple(resource=to_object_ref(record), relation="reader", subject=to_subject_ref(recipient)),
        )
    revoked = result_data(execute_schema(campaign_schema, query, user=recipient))["thread_notifications"]
    assert {row["id"] for row in revoked} == {row.sqid for row in notices}
    assert all(row["message"] is None for row in revoked)


def test_notification_by_id_and_aggregate_use_recipient_permission(composed_tables, campaign_schema):
    recipient, owner = make_user("private-inbox"), make_user("thread-reader")
    with system_context(reason="test.messaging.graphql.inbox-scope"):
        thread = Thread.objects.create(owner=owner)
        message = Message.objects.create(thread=thread)
        notice = ThreadNotification.objects.create(thread=thread, message=message, user=recipient)
    assert notice.with_actor(owner).has_access("read")
    query = """query Notice($id: String!) {
        thread_notifications_by_pk(id: $id) { id }
        thread_notifications_aggregate { aggregate { count } }
    }"""
    for actor, expected in ((owner, None), (recipient, {"id": notice.sqid})):
        data = result_data(execute_schema(campaign_schema, query, {"id": notice.sqid}, user=actor))
        assert data["thread_notifications_by_pk"] == expected
        assert data["thread_notifications_aggregate"]["aggregate"]["count"] == int(expected is not None)


def test_notification_acknowledgement_returns_validation_for_foreign_or_missing_ids(composed_tables, campaign_schema):
    recipient, stranger = make_user("ack-recipient"), make_user("ack-stranger")
    with system_context(reason="test.messaging.graphql.ack"):
        thread = Thread.objects.create()
        message = Message.objects.create(thread=thread)
        notice = ThreadNotification.objects.create(thread=thread, message=message, user=recipient)
        vanished = ThreadNotification.objects.create(thread=thread, message=Message.objects.create(), user=recipient)
        missing_id = vanished.sqid
        vanished.delete()
    mutation = """mutation Ack($id: ID!) { mark_thread_notification_read(id: $id) { id read_at } }"""
    for actor, public_id in ((stranger, notice.sqid), (create_platform_admin("ack-admin"), notice.sqid),
                             (recipient, missing_id)):
        result = execute_schema(campaign_schema, mutation, {"id": public_id}, user=actor)
        assert result.errors
        assert all(error.extensions.get("code") == "VALIDATION" for error in result.errors)
    data = result_data(execute_schema(campaign_schema, mutation, {"id": notice.sqid}, user=recipient))
    assert data["mark_thread_notification_read"]["read_at"] is not None


@pytest.mark.parametrize("projection,fields", [
    ("ThreadAttachmentType", ("thread",)),
    ("ThreadFollowerType", ("thread", "attachment", "last_read_message")),
    ("ThreadActivityType", ("thread", "attachment")),
    ("MessageEdgeType", ("src", "dst")),
    ("MessageStarType", ("message",)),
    ("ParticipantType", ("handle",)),
    ("ReactionType", ("handle",)),
    ("RecordThreadNotificationType", ("message", "follower")),
])
def test_unlisted_projection_types_regate_sudo_cached_relations(composed_tables, projection, fields):
    """Invoke the installed fields, including types reached only inside chatter."""
    viewer, owner = make_user("projection-viewer"), make_user("projection-owner")
    with system_context(reason="test.messaging.graphql.cached-relations"):
        thread = Thread.objects.create(owner=owner)
        message = Message.objects.create(thread=thread)
        other = Message.objects.create(thread=thread)
        handle = Handle.objects.create(platform="email", value="hidden-handle@example.com", created_by=owner)
        attachment = ThreadAttachment.objects.create(
            thread=thread, role="source", content_type=ContentType.objects.get_for_model(Thread), object_id=thread.pk,
        )
        follower = ThreadFollower.objects.create(thread=thread, attachment=attachment,
                                                 party=Party.objects.for_user(viewer), last_read_message=message)
        hidden_follow = ThreadFollower.objects.create(thread=thread, party=Party.objects.for_user(owner))
        rows = {
            "ThreadAttachmentType": attachment,
            "ThreadFollowerType": follower,
            "ThreadActivityType": ThreadActivity.objects.create(thread=thread, attachment=attachment, user=viewer),
            "MessageEdgeType": MessageEdge.objects.create(src=message, dst=other, kind="quote"),
            "MessageStarType": MessageStar.objects.create(message=message, user=viewer),
            "ParticipantType": Participant.objects.create(message=message, handle=handle),
            "ReactionType": Reaction.objects.create(message=message, handle=handle, reaction="ok"),
            "RecordThreadNotificationType": ThreadNotification.objects.create(
                thread=thread, message=message, follower=hidden_follow, user=viewer,
            ),
        }
        row = type(rows[projection])._base_manager.select_related(*fields).get(pk=rows[projection].pk)
    if projection in {"ThreadAttachmentType", "MessageEdgeType", "ParticipantType", "ReactionType"}:
        grant(row, "reader", viewer)
    assert row.with_actor(viewer).has_access("read")
    with actor_context(viewer):
        definition = getattr(messaging_schema, projection).__strawberry_definition__
        for name in fields:
            assert definition.get_field(name).base_resolver(row) is None, (projection, name)


def test_deleting_a_native_thread_does_not_leave_admin_only_message_orphans(composed_tables, campaign_schema):
    owner = make_user("thread-teardown-owner")
    with system_context(reason="test.messaging.graphql.thread-teardown"):
        thread = Thread.objects.create(owner=owner)
        message = Message.objects.create(thread=thread)
    result = result_data(execute_schema(campaign_schema, """
        mutation Delete($id: String!) { delete_threads_by_pk(id: $id) { id } }
    """, {"id": thread.sqid}, user=owner))
    assert result["delete_threads_by_pk"] == {"id": thread.sqid}
    assert not Message._base_manager.filter(pk=message.pk).exists()
