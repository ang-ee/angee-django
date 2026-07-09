"""Tests for the messaging GraphQL Hasura resources."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, override_settings
from rebac import (
    RelationshipTuple,
    actor_context,
    app_settings,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging.models import Channel as AbstractChannel
from tests import test_messaging as messaging_models
from tests import test_parties_graphql as parties_graphql
from tests.conftest import (
    Backend,
    Drive,
    Integration,
    MimeType,
    SchemaAddon,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
)
from tests.conftest import (
    File as StorageFile,
)
from tests.conftest import result_data as _data

_ChannelMeta = getattr(AbstractChannel, "Meta", object)


class Channel(Integration, AbstractChannel):
    """Concrete message channel used to import the messaging schema."""

    class Meta(_ChannelMeta):
        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_channel"
        rebac_resource_type = "messaging/channel"
        rebac_id_attr = "sqid"


messaging_schema = importlib.import_module("angee.messaging.schema")
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")
parties_schema = parties_graphql.parties_schema
User = get_user_model()

MESSAGING_GRAPHQL_MODELS = (
    *messaging_models.MESSAGING_TEST_MODELS,
    Channel,
)


def test_console_resource_metadata_declares_message_surface() -> None:
    """The composed console schema reports Message's Hasura resource contract."""

    schema = _schema()
    metadata = {
        item.model_label: item
        for item in schema.angee_resources
    }["messaging.Message"]

    assert metadata.roots.list_name == "messages"
    assert metadata.roots.detail_name == "messages_by_pk"
    assert metadata.roots.aggregate_name == "messages_aggregate"
    assert metadata.roots.group_name == "messages_groups"
    assert metadata.roots.create_name is None
    assert metadata.roots.update_name == "update_messages_by_pk"
    assert metadata.roots.delete_name == "delete_messages_by_pk"
    assert metadata.filter_fields == (
        "id",
        "subject",
        "status",
        "message_type",
        "subtype",
        "platform",
        "direction",
        "thread",
        "channel",
        "sender",
        "sent_at",
    )
    assert metadata.order_fields == ("sent_at", "received_at", "created_at")
    assert metadata.aggregate_fields == ("id",)
    assert metadata.group_by_fields == (
        "thread",
        "thread__subject",
        "sender",
        "sender__display_name",
        "channel",
        "channel__display_name",
        "status",
        "message_type",
        "subtype",
        "subtype__key",
        "platform",
        "metadata.mailbox",
        "sent_at",
    )
    assert metadata.update_fields == ("status", "subject")
    assert metadata.capabilities == ("list", "detail", "aggregate", "groups", "update", "delete", "changes")
    assert {
        axis.field: (axis.model_label, axis.public_id_field, axis.label_axis)
        for axis in metadata.relation_axes
    } == {
        "thread": ("messaging.Thread", "sqid", "thread__subject"),
        "sender": ("parties.Handle", "sqid", "sender__display_name"),
        "channel": ("integrate.Integration", "sqid", "channel__display_name"),
        "subtype": ("messaging.MessageSubtype", "sqid", "subtype__key"),
    }

    serialized = schema._schema.extensions["angee"]["resources"]
    message = {
        item["modelLabel"]: item
        for item in serialized
    }["messaging.Message"]
    assert message["roots"]["detail"] == "messages_by_pk"
    assert message["roots"]["aggregate"] == "messages_aggregate"
    assert message["roots"]["groups"] == "messages_groups"
    assert message["roots"]["create"] is None
    assert message["roots"]["update"] == "update_messages_by_pk"
    assert message["roots"]["delete"] == "delete_messages_by_pk"
    assert message["roots"]["changes"] == "messageChanged"
    assert message["typeNames"]["filter"] == "messages_bool_exp"
    assert message["typeNames"]["order"] == "messages_order_by"
    assert message["groupByFields"] == [
        "thread",
        "thread__subject",
        "sender",
        "sender__display_name",
        "channel",
        "channel__display_name",
        "status",
        "message_type",
        "subtype",
        "subtype__key",
        "platform",
        "metadata.mailbox",
        "sent_at",
    ]
    mailbox_dimension = {dimension["field"]: dimension for dimension in message["groupDimensions"]}["metadata.mailbox"]
    assert mailbox_dimension["input"] == "METADATA__MAILBOX"
    assert mailbox_dimension["key"] == "metadata__mailbox"
    assert mailbox_dimension["kind"] == "json"
    assert mailbox_dimension["filter"] == {
        "kind": "equality",
        "field": "metadata",
        "valueKey": "metadata__mailbox",
        "rangeKey": None,
        "lookup": "jsonContains",
        "nullLookup": None,
        "valueTransform": "jsonObject:mailbox",
        "valueMap": [],
    }
    assert message["updateFields"] == ["status", "subject"]
    status_field = {field["name"]: field for field in message["fields"]}["status"]
    assert status_field["filterable"] is True
    assert status_field["groupable"] is True
    assert status_field["updatable"] is True


def test_console_resource_metadata_declares_thread_and_channel_surfaces() -> None:
    """Threads and channels expose their Hasura roots through resource metadata."""

    resources = {item.model_label: item for item in _schema().angee_resources}

    thread = resources["messaging.Thread"]
    assert thread.roots.list_name == "threads"
    assert thread.roots.detail_name == "threads_by_pk"
    assert thread.roots.update_name == "update_threads_by_pk"
    assert thread.roots.delete_name == "delete_threads_by_pk"
    assert thread.create_fields == ()
    assert thread.update_fields == ("subject", "visibility")
    assert thread.group_by_fields == ("channel", "channel__display_name", "modality", "visibility", "last_message_at")

    channel = resources["messaging.Channel"]
    assert channel.roots.list_name == "channels"
    assert channel.roots.detail_name == "channels_by_pk"
    assert channel.roots.create_name is None
    assert channel.roots.update_name is None
    assert channel.roots.delete_name is None
    assert channel.capabilities == ("list", "detail", "aggregate", "groups")


def test_messaging_schema_does_not_expose_optional_imap_connect() -> None:
    """Base messaging stays transport-neutral; IMAP contributes its own mutation."""

    assert "connect_imap_channel" not in _schema().as_str()


def test_message_and_thread_hasura_writes(messaging_graphql_tables: None) -> None:
    """Message and thread human edits use generated Hasura mutation roots."""

    admin = _platform_admin("msg-hasura-admin")
    thread, message = _seed_thread_and_message(admin)
    schema = _schema()

    updated_message = _data(
        execute_schema(
            schema,
            """
            mutation Hide($id: String!) {
              update_messages_by_pk(pk_columns: {id: $id}, _set: {status: "hidden", subject: "Redacted"}) {
                status
                subject
              }
            }
            """,
            {"id": message.sqid},
            request=_request(admin),
        )
    )["update_messages_by_pk"]
    assert updated_message == {"status": "HIDDEN", "subject": "Redacted"}

    updated_thread = _data(
        execute_schema(
            schema,
            """
            mutation Rename($id: String!) {
              update_threads_by_pk(pk_columns: {id: $id}, _set: {subject: "Inbox", visibility: "public"}) {
                subject
                visibility
              }
            }
            """,
            {"id": thread.sqid},
            request=_request(admin),
        )
    )["update_threads_by_pk"]
    assert updated_thread == {"subject": "Inbox", "visibility": "PUBLIC"}

    deleted = _data(
        execute_schema(
            schema,
            """
            mutation Delete($id: String!) {
              delete_messages_by_pk(id: $id) { id subject }
            }
            """,
            {"id": message.sqid},
            request=_request(admin),
        )
    )["delete_messages_by_pk"]
    assert deleted == {"id": message.sqid, "subject": "Redacted"}

    with system_context(reason="test.messaging.hasura_write.verify"):
        assert messaging_models.Thread.objects.get(sqid=thread.sqid).visibility == "public"
        assert not messaging_models.Message.objects.filter(sqid=message.sqid).exists()


def test_record_chatter_query_and_post(messaging_graphql_tables: None) -> None:
    """The custom record chatter fields resolve and post through the threaded model mixin."""

    admin = _platform_admin("msg-chatter-admin")
    with system_context(reason="test.messaging.record_chatter.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 101")
    schema = _schema()

    before = _data(
        execute_schema(
            schema,
            """
            query RecordThread($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                thread { id }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]
    assert before == {"error_code": None, "thread": None}

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage($model: String!, $id: ID!, $body: String!) {
              post_record_message(input: {model_label: $model, record_id: $id, body: $body}) {
                error_code
                follower_count
                is_following
                followers { user { username } }
                message {
                  subject
                  preview
                  parts { fragment { text } }
                }
                thread {
                  subject
                  message_count
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Follow up from GraphQL.",
            },
            request=_request(admin),
        )
    )["post_record_message"]
    assert posted["error_code"] is None
    assert posted["message"]["subject"] == "Case 101"
    assert posted["message"]["preview"] == "Follow up from GraphQL."
    assert posted["message"]["parts"][0]["fragment"]["text"] == "Follow up from GraphQL."
    assert posted["thread"] == {"subject": "Case 101", "message_count": 1}
    assert posted["follower_count"] == 1
    assert posted["is_following"] is True
    assert posted["followers"] == [{"user": {"username": "msg-chatter-admin"}}]

    after = _data(
        execute_schema(
            schema,
            """
            query RecordThread($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                follower_count
                is_following
                thread {
                  subject
                  message_count
                  messages { preview }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]
    assert after["error_code"] is None
    assert after["thread"]["subject"] == "Case 101"
    assert after["thread"]["message_count"] == 1
    assert after["thread"]["messages"] == [{"preview": "Follow up from GraphQL."}]
    assert after["follower_count"] == 1
    assert after["is_following"] is True


def test_record_chatter_post_note(messaging_graphql_tables: None) -> None:
    """The record chatter API logs internal notes without auto-following the author."""

    admin = _platform_admin("msg-note-admin")
    with system_context(reason="test.messaging.record_chatter_note.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 102")
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordNote($model: String!, $id: ID!, $body: String!) {
              post_record_message(
                input: {model_label: $model, record_id: $id, body: $body, kind: "note"}
              ) {
                error
                error_code
                follower_count
                is_following
                message {
                  message_type
                  preview
                  subtype {
                    key
                    description
                  }
                }
                thread {
                  message_count
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Internal note from GraphQL.",
            },
            request=_request(admin),
        )
    )["post_record_message"]

    assert posted == {
        "error": None,
        "error_code": None,
        "follower_count": 0,
        "is_following": False,
        "message": {
            "message_type": "NOTIFICATION",
            "preview": "Internal note from GraphQL.",
            "subtype": {"key": "note", "description": "Internal note"},
        },
        "thread": {"message_count": 1},
    }


def test_record_chatter_post_reply(messaging_graphql_tables: None) -> None:
    """The record chatter API stores replies against their parent message."""

    admin = _platform_admin("msg-reply-admin")
    with system_context(reason="test.messaging.record_chatter_reply.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 103")
    schema = _schema()

    root = _data(
        execute_schema(
            schema,
            """
            mutation PostRoot($model: String!, $id: ID!) {
              post_record_message(
                input: {model_label: $model, record_id: $id, body: "Original from GraphQL."}
              ) {
                message {
                  id
                  preview
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["post_record_message"]["message"]

    reply = _data(
        execute_schema(
            schema,
            """
            mutation PostReply($model: String!, $id: ID!, $parent: ID!) {
              post_record_message(
                input: {
                  model_label: $model
                  record_id: $id
                  body: "Reply from GraphQL."
                  parent_message_id: $parent
                }
              ) {
                error
                error_code
                message {
                  id
                  preview
                  parent {
                    id
                    preview
                  }
                }
                thread {
                  messages {
                    preview
                    parent { preview }
                  }
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "parent": root["id"],
            },
            request=_request(admin),
        )
    )["post_record_message"]

    assert reply["error_code"] is None
    assert reply["error"] is None
    assert reply["message"]["preview"] == "Reply from GraphQL."
    assert reply["message"]["parent"] == root
    assert reply["thread"]["messages"] == [
        {"preview": "Reply from GraphQL.", "parent": {"preview": "Original from GraphQL."}},
        {"preview": "Original from GraphQL.", "parent": None},
    ]


def test_record_chatter_toggles_message_reaction(messaging_graphql_tables: None) -> None:
    """The record chatter API exposes Odoo-style grouped message reactions."""

    admin = _platform_admin("msg-react-admin")
    other = _platform_admin("msg-react-other")
    with system_context(reason="test.messaging.record_chatter_reaction.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 104")
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostMessage($model: String!, $id: ID!) {
              post_record_message(
                input: {model_label: $model, record_id: $id, body: "React to this."}
              ) {
                message { id }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["post_record_message"]["message"]

    first = _data(
        execute_schema(
            schema,
            """
            mutation React($model: String!, $id: ID!, $message: ID!, $reaction: String!) {
              set_record_message_reaction(
                input: {
                  model_label: $model
                  record_id: $id
                  message_id: $message
                  reaction: $reaction
                }
              ) {
                error
                error_code
                reaction_groups {
                  reaction
                  count
                  self_reacted
                  handles { value display_name }
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": posted["id"],
                "reaction": "👍",
            },
            request=_request(admin),
        )
    )["set_record_message_reaction"]

    assert first == {
        "error": None,
        "error_code": None,
        "reaction_groups": [
            {
                "reaction": "👍",
                "count": 1,
                "self_reacted": True,
                "handles": [{"value": "msg-react-admin@example.com", "display_name": "msg-react-admin"}],
            }
        ],
    }

    _data(
        execute_schema(
            schema,
            """
            mutation React($model: String!, $id: ID!, $message: ID!, $reaction: String!) {
              set_record_message_reaction(
                input: {
                  model_label: $model
                  record_id: $id
                  message_id: $message
                  reaction: $reaction
                  action: "add"
                }
              ) {
                error_code
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": posted["id"],
                "reaction": "👍",
            },
            request=_request(other),
        )
    )

    grouped = _data(
        execute_schema(
            schema,
            """
            query Thread($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                messages {
                  reaction_groups {
                    reaction
                    count
                    self_reacted
                    handles { value }
                  }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]["messages"][0]["reaction_groups"]

    assert grouped == [
        {
            "reaction": "👍",
            "count": 2,
            "self_reacted": True,
            "handles": [
                {"value": "msg-react-admin@example.com"},
                {"value": "msg-react-other@example.com"},
            ],
        }
    ]

    removed = _data(
        execute_schema(
            schema,
            """
            mutation React($model: String!, $id: ID!, $message: ID!, $reaction: String!) {
              set_record_message_reaction(
                input: {
                  model_label: $model
                  record_id: $id
                  message_id: $message
                  reaction: $reaction
                }
              ) {
                reaction_groups {
                  reaction
                  count
                  self_reacted
                  handles { value }
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": posted["id"],
                "reaction": "👍",
            },
            request=_request(admin),
        )
    )["set_record_message_reaction"]["reaction_groups"]

    assert removed == [
        {
            "reaction": "👍",
            "count": 1,
            "self_reacted": False,
            "handles": [{"value": "msg-react-other@example.com"}],
        }
    ]


def test_record_chatter_toggles_message_starred(messaging_graphql_tables: None) -> None:
    """The record chatter API exposes Odoo-style current-user message stars."""

    admin = _platform_admin("msg-star-admin")
    other = _platform_admin("msg-star-other")
    with system_context(reason="test.messaging.record_star.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 109")
        message = ticket.message_post("Star this.")
    schema = _schema()

    starred = _data(
        execute_schema(
            schema,
            """
            mutation StarRecordMessage($model: String!, $id: ID!, $message: ID!) {
              set_record_message_starred(
                input: {model_label: $model, record_id: $id, message_id: $message}
              ) {
                error
                error_code
                starred
                message {
                  id
                  starred
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": message.sqid,
            },
            request=_request(admin),
        )
    )["set_record_message_starred"]

    assert starred == {
        "error": None,
        "error_code": None,
        "starred": True,
        "message": {"id": message.sqid, "starred": True},
    }

    thread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadStars($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                messages {
                  id
                  starred
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]
    assert thread == {
        "error_code": None,
        "messages": [{"id": message.sqid, "starred": True}],
    }

    other_thread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadStars($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                messages {
                  id
                  starred
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(other),
        )
    )["record_thread"]
    assert other_thread == {
        "error_code": None,
        "messages": [{"id": message.sqid, "starred": False}],
    }

    unstarred = _data(
        execute_schema(
            schema,
            """
            mutation UnstarRecordMessage($model: String!, $id: ID!, $message: ID!) {
              set_record_message_starred(
                input: {
                  model_label: $model
                  record_id: $id
                  message_id: $message
                  starred: false
                }
              ) {
                error_code
                starred
                message { starred }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": message.sqid,
            },
            request=_request(admin),
        )
    )["set_record_message_starred"]

    assert unstarred == {
        "error_code": None,
        "starred": False,
        "message": {"starred": False},
    }
    assert not messaging_models.MessageStar._base_manager.filter(message=message, user=admin).exists()


def test_record_chatter_update_message(messaging_graphql_tables: None) -> None:
    """The record chatter API edits comment content without duplicating history."""

    admin = _platform_admin("msg-edit-admin")
    with system_context(reason="test.messaging.record_chatter_edit.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 111")
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage($model: String!, $id: ID!, $body: String!) {
              post_record_message(input: {model_label: $model, record_id: $id, body: $body}) {
                message { id preview }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Original GraphQL body.",
            },
            request=_request(admin),
        )
    )["post_record_message"]

    updated = _data(
        execute_schema(
            schema,
            """
            mutation UpdateRecordMessage($model: String!, $id: ID!, $message: ID!, $body: String!) {
              update_record_message(
                input: {model_label: $model, record_id: $id, message_id: $message, body: $body}
              ) {
                error
                error_code
                message {
                  id
                  status
                  preview
                  parts { fragment { text } }
                }
                thread {
                  message_count
                  messages {
                    id
                    status
                    preview
                  }
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": posted["message"]["id"],
                "body": "Updated GraphQL body.",
            },
            request=_request(admin),
        )
    )["update_record_message"]

    assert updated["error_code"] is None
    assert updated["error"] is None
    assert updated["message"]["id"] == posted["message"]["id"]
    assert updated["message"]["status"] == "EDITED"
    assert updated["message"]["preview"] == "Updated GraphQL body."
    assert updated["message"]["parts"][0]["fragment"]["text"] == "Updated GraphQL body."
    assert updated["thread"]["message_count"] == 1
    assert updated["thread"]["messages"] == [
        {
            "id": posted["message"]["id"],
            "status": "EDITED",
            "preview": "Updated GraphQL body.",
        }
    ]


def test_record_chatter_deletes_message(messaging_graphql_tables: None) -> None:
    """The record chatter API unlinks a message through the record-owned guard."""

    admin = _platform_admin("msg-delete-admin")
    with system_context(reason="test.messaging.record_chatter_delete.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 113")
    schema = _schema()

    first = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage($model: String!, $id: ID!, $body: String!) {
              post_record_message(input: {model_label: $model, record_id: $id, body: $body}) {
                message { id preview }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Delete me.",
            },
            request=_request(admin),
        )
    )["post_record_message"]["message"]
    second = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage($model: String!, $id: ID!, $body: String!) {
              post_record_message(input: {model_label: $model, record_id: $id, body: $body}) {
                message { id preview }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Keep me.",
            },
            request=_request(admin),
        )
    )["post_record_message"]["message"]

    deleted = _data(
        execute_schema(
            schema,
            """
            mutation DeleteRecordMessage($model: String!, $id: ID!, $message: ID!) {
              delete_record_message(
                input: {model_label: $model, record_id: $id, message_id: $message}
              ) {
                error
                error_code
                deleted_message_id
                message_result_count
                thread {
                  message_count
                  messages { id preview }
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": first["id"],
            },
            request=_request(admin),
        )
    )["delete_record_message"]

    assert deleted["error_code"] is None
    assert deleted["error"] is None
    assert deleted["deleted_message_id"] == first["id"]
    assert deleted["message_result_count"] == 1
    assert deleted["thread"] == {
        "message_count": 1,
        "messages": [{"id": second["id"], "preview": "Keep me."}],
    }
    first_exists = messaging_models.Message._base_manager.filter(
        **messaging_models.Message.public_id_lookup(first["id"])
    ).exists()
    assert first_exists is False


def test_record_chatter_update_rejects_tracking_message(messaging_graphql_tables: None) -> None:
    """The GraphQL edit mutation keeps tracking messages immutable."""

    admin = _platform_admin("msg-edit-guard-admin")
    with system_context(reason="test.messaging.record_chatter_edit_guard.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 112")
        message = ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "done",
                    "old_display": "New",
                    "new_display": "Done",
                },
            ),
        )
    schema = _schema()

    payload = _data(
        execute_schema(
            schema,
            """
            mutation UpdateRecordMessage($model: String!, $id: ID!, $message: ID!, $body: String!) {
              update_record_message(
                input: {model_label: $model, record_id: $id, message_id: $message, body: $body}
              ) {
                error
                error_code
                message { id }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": message.sqid,
                "body": "Tampered",
            },
            request=_request(admin),
        )
    )["update_record_message"]

    assert payload["error_code"] == "BAD_MESSAGE"
    assert payload["error"] == "Only comment messages can be edited."
    assert payload["message"] is None


def test_record_chatter_query_returns_tracking_values(messaging_graphql_tables: None) -> None:
    """The record chatter query returns structured tracking rows for auto-comments."""

    admin = _platform_admin("msg-tracking-admin")
    with system_context(reason="test.messaging.record_tracking.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 505")
        ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "won",
                    "old_display": "New",
                    "new_display": "Won",
                },
            ),
        )
    schema = _schema()

    payload = _data(
        execute_schema(
            schema,
            """
            query RecordThreadTracking($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                thread {
                  messages {
                    message_type
                    preview
                    subtype {
                      key
                      description
                    }
                    tracking_values {
                      field_name
                      field_label
                      field_type
                      old_display
                      new_display
                    }
                  }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]

    assert payload["error_code"] is None
    assert payload["thread"]["messages"] == [
        {
            "message_type": "AUTO_COMMENT",
            "preview": "Stage: New -> Won",
            "subtype": {
                "key": "record_updated",
                "description": "Record updated",
            },
            "tracking_values": [
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_display": "New",
                    "new_display": "Won",
                },
            ],
        }
    ]


def test_record_chatter_searches_messages_and_tracking_values(messaging_graphql_tables: None) -> None:
    """The record chatter API searches comment bodies and tracking rows."""

    admin = _platform_admin("msg-search-admin")
    with system_context(reason="test.messaging.record_search.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 515")
        ticket.message_post("General update.")
        ticket.message_post("Rollout needle is blocked.")
        ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "won",
                    "old_display": "New",
                    "new_display": "Won",
                },
            ),
        )
    schema = _schema()

    def search(term: str) -> dict[str, Any]:
        return _data(
            execute_schema(
                schema,
                """
                query RecordThreadSearch($model: String!, $id: ID!, $search: String!) {
                  record_thread(input: {model_label: $model, record_id: $id, search: $search}) {
                    error_code
                    message_result_count
                    messages {
                      message_type
                      preview
                      parts { fragment { text } }
                      tracking_values {
                        field_label
                        old_display
                        new_display
                      }
                    }
                  }
                }
                """,
                {"model": "messaging.ThreadedTicket", "id": ticket.sqid, "search": term},
                request=_request(admin),
            )
        )["record_thread"]

    body_result = search("rollout needle")
    assert body_result["error_code"] is None
    assert body_result["message_result_count"] == 1
    assert body_result["messages"][0]["message_type"] == "COMMENT"
    assert body_result["messages"][0]["parts"][0]["fragment"]["text"] == "Rollout needle is blocked."

    tracking_result = search("Won")
    assert tracking_result["error_code"] is None
    assert tracking_result["message_result_count"] == 1
    assert tracking_result["messages"][0]["message_type"] == "AUTO_COMMENT"
    assert tracking_result["messages"][0]["tracking_values"] == [
        {"field_label": "Stage", "old_display": "New", "new_display": "Won"}
    ]


def test_record_chatter_fetches_message_windows(messaging_graphql_tables: None) -> None:
    """The record chatter API supports Odoo-style before/after/around windows."""

    admin = _platform_admin("msg-window-admin")
    with system_context(reason="test.messaging.record_window.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 516")
        messages = [
            ticket.message_post(f"Window message {index}")
            for index in range(1, 6)
        ]
    schema = _schema()

    def fetch(
        *,
        limit: int = 2,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
    ) -> dict[str, Any]:
        return _data(
            execute_schema(
                schema,
                """
                query RecordThreadWindow(
                  $model: String!
                  $id: ID!
                  $limit: Int!
                  $before: ID
                  $after: ID
                  $around: ID
                ) {
                  record_thread(
                    input: {
                      model_label: $model
                      record_id: $id
                      message_limit: $limit
                      before: $before
                      after: $after
                      around: $around
                    }
                  ) {
                    error_code
                    message_result_count
                    messages {
                      id
                      preview
                    }
                  }
                }
                """,
                {
                    "model": "messaging.ThreadedTicket",
                    "id": ticket.sqid,
                    "limit": limit,
                    "before": before,
                    "after": after,
                    "around": around,
                },
                request=_request(admin),
            )
        )["record_thread"]

    first_page = fetch(limit=2)
    assert first_page["error_code"] is None
    assert first_page["message_result_count"] == 5
    # The newest window is still selected, but returned chronological ascending.
    assert [message["preview"] for message in first_page["messages"]] == [
        "Window message 4",
        "Window message 5",
    ]

    older_page = fetch(limit=2, before=messages[3].sqid)
    assert [message["preview"] for message in older_page["messages"]] == [
        "Window message 2",
        "Window message 3",
    ]

    newer_page = fetch(limit=2, after=messages[1].sqid)
    assert [message["preview"] for message in newer_page["messages"]] == [
        "Window message 3",
        "Window message 4",
    ]

    around_page = fetch(limit=4, around=messages[2].sqid)
    assert [message["preview"] for message in around_page["messages"]] == [
        "Window message 2",
        "Window message 3",
        "Window message 4",
        "Window message 5",
    ]


def test_record_chatter_orders_interleaved_backfilled_email(messaging_graphql_tables: None) -> None:
    """A late-synced email (older send time, newer row) windows by send time, not pk."""

    admin = _platform_admin("msg-backfill-admin")
    base = datetime(2024, 5, 1, 12, 0, tzinfo=timezone.utc)
    with system_context(reason="test.messaging.record_backfill.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 942")
        messages = [ticket.message_post(f"Message {index}") for index in range(1, 6)]
        # A backfilled email arrives last (highest pk) but was sent between #2 and #3.
        backfilled = ticket.message_post("Backfilled email")
        for index, message in enumerate(messages, start=1):
            messaging_models.Message.objects.filter(pk=message.pk).update(
                sent_at=base + timedelta(minutes=index)
            )
        messaging_models.Message.objects.filter(pk=backfilled.pk).update(
            sent_at=base + timedelta(minutes=2, seconds=30)
        )
    schema = _schema()

    def previews(**window: Any) -> list[str]:
        payload = _data(
            execute_schema(
                schema,
                """
                query RecordThreadWindow($model: String!, $id: ID!, $limit: Int!, $before: ID) {
                  record_thread(
                    input: {
                      model_label: $model
                      record_id: $id
                      message_limit: $limit
                      before: $before
                    }
                  ) {
                    messages {
                      preview
                    }
                  }
                }
                """,
                {
                    "model": "messaging.ThreadedTicket",
                    "id": ticket.sqid,
                    "before": None,
                    **window,
                },
                request=_request(admin),
            )
        )["record_thread"]
        return [message["preview"] for message in payload["messages"]]

    # Chronological order is Message 1, 2, Backfilled email, 3, 4, 5. The newest window
    # is the three latest by send time — the backfilled email is *not* among them
    # despite carrying the highest pk.
    assert previews(limit=3) == ["Message 3", "Message 4", "Message 5"]
    # Cursoring before Message 3 returns the two rows chronologically before it, so the
    # interleaved backfilled email cannot be skipped at the page boundary.
    assert previews(limit=2, before=messages[2].sqid) == ["Message 2", "Backfilled email"]


def test_record_thread_projects_edit_and_delete_capability(messaging_graphql_tables: None) -> None:
    """can_edit/can_delete mirror the update/delete mutation authorization."""

    admin = _platform_admin("msg-capability-admin")
    with system_context(reason="test.messaging.record_capability.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 808")
        ticket.message_post("Editable comment.")
        ticket.message_track(
            (
                {
                    "field_name": "stage",
                    "field_label": "Stage",
                    "field_type": "selection",
                    "old_value": "new",
                    "new_value": "won",
                    "old_display": "New",
                    "new_display": "Won",
                },
            ),
        )
    schema = _schema()

    payload = _data(
        execute_schema(
            schema,
            """
            query RecordThreadCapability($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                messages {
                  message_type
                  can_edit
                  can_delete
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]

    assert payload["error_code"] is None
    capabilities = {
        message["message_type"]: (message["can_edit"], message["can_delete"])
        for message in payload["messages"]
    }
    # A plain comment is editable and deletable; a tracked message is deletable
    # (post access) but never editable (the mail edit rule blocks it).
    assert capabilities["COMMENT"] == (True, True)
    assert capabilities["AUTO_COMMENT"] == (False, True)


def test_record_chatter_notifications_can_be_marked_read(messaging_graphql_tables: None) -> None:
    """The record chatter API exposes current-user needaction state."""

    poster = _platform_admin("msg-notify-poster")
    watcher = _platform_admin("msg-notify-watcher")
    with system_context(reason="test.messaging.record_notifications.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 606")
        ticket.message_subscribe(user=watcher)
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage($model: String!, $id: ID!, $body: String!) {
              post_record_message(input: {model_label: $model, record_id: $id, body: $body}) {
                error_code
                unread_count
                needaction_count
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Please review this.",
            },
            request=_request(poster),
        )
    )["post_record_message"]
    assert posted == {
        "error_code": None,
        "unread_count": 0,
        "needaction_count": 0,
    }

    unread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadNotifications($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                unread_count
                needaction_count
                notifications {
                  is_read
                  notification_type
                  notification_status
                  message { preview }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(watcher),
        )
    )["record_thread"]
    assert unread == {
        "error_code": None,
        "unread_count": 1,
        "needaction_count": 1,
        "notifications": [
            {
                "is_read": False,
                "notification_type": "INBOX",
                "notification_status": "READY",
                "message": {"preview": "Please review this."},
            }
        ],
    }

    read = _data(
        execute_schema(
            schema,
            """
            mutation MarkRecordThreadRead($model: String!, $id: ID!) {
              mark_record_thread_read(input: {model_label: $model, record_id: $id}) {
                error_code
                unread_count
                needaction_count
                notifications {
                  is_read
                  read_at
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(watcher),
        )
    )["mark_record_thread_read"]
    assert read["error_code"] is None
    assert read["unread_count"] == 0
    assert read["needaction_count"] == 0
    assert len(read["notifications"]) == 1
    assert read["notifications"][0]["is_read"] is True
    assert read["notifications"][0]["read_at"] is not None


def test_record_chatter_marks_one_message_done(messaging_graphql_tables: None) -> None:
    """The record chatter API can clear needaction for one message only."""

    poster = _platform_admin("msg-done-poster")
    watcher = _platform_admin("msg-done-watcher")
    with system_context(reason="test.messaging.record_message_done.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 616")
        ticket.message_subscribe(user=watcher)
    with actor_context(poster):
        first = ticket.message_post("First needaction item.")
        second = ticket.message_post("Second needaction item.")
    schema = _schema()

    unread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadNeedaction($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                unread_count
                needaction_count
                messages {
                  id
                  preview
                  needaction
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(watcher),
        )
    )["record_thread"]
    assert unread["error_code"] is None
    assert unread["unread_count"] == 2
    assert unread["needaction_count"] == 2
    assert {message["preview"]: message["needaction"] for message in unread["messages"]} == {
        "First needaction item.": True,
        "Second needaction item.": True,
    }

    done = _data(
        execute_schema(
            schema,
            """
            mutation MarkRecordMessageDone($model: String!, $id: ID!, $message: ID!) {
              mark_record_message_done(
                input: {model_label: $model, record_id: $id, message_id: $message}
              ) {
                error_code
                unread_count
                needaction_count
                message {
                  id
                  needaction
                }
                notifications {
                  message { id }
                  is_read
                  read_at
                }
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "message": first.sqid,
            },
            request=_request(watcher),
        )
    )["mark_record_message_done"]
    assert done["error_code"] is None
    assert done["unread_count"] == 1
    assert done["needaction_count"] == 1
    assert done["message"] == {"id": first.sqid, "needaction": False}
    notifications = {row["message"]["id"]: row for row in done["notifications"]}
    assert notifications[first.sqid]["is_read"] is True
    assert notifications[first.sqid]["read_at"] is not None
    assert notifications[second.sqid]["is_read"] is False
    assert notifications[second.sqid]["read_at"] is None

    refreshed = _data(
        execute_schema(
            schema,
            """
            query RecordThreadNeedaction($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                messages {
                  id
                  needaction
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(watcher),
        )
    )["record_thread"]
    assert {message["id"]: message["needaction"] for message in refreshed["messages"]} == {
        first.sqid: False,
        second.sqid: True,
    }


def test_record_chatter_post_notifies_direct_recipient(messaging_graphql_tables: None) -> None:
    """The record post mutation accepts explicit user recipients."""

    poster = _platform_admin("msg-direct-poster")
    recipient = _platform_admin("msg-direct-recipient")
    with system_context(reason="test.messaging.record_direct_recipient.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 707")
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordMessage(
              $model: String!
              $id: ID!
              $body: String!
              $recipient: ID!
            ) {
              post_record_message(
                input: {
                  model_label: $model
                  record_id: $id
                  body: $body
                  recipient_user_ids: [$recipient]
                }
              ) {
                error_code
                unread_count
                needaction_count
              }
            }
            """,
            {
                "model": "messaging.ThreadedTicket",
                "id": ticket.sqid,
                "body": "Direct heads-up.",
                "recipient": str(recipient.sqid),
            },
            request=_request(poster),
        )
    )["post_record_message"]
    assert posted == {
        "error_code": None,
        "unread_count": 0,
        "needaction_count": 0,
    }

    unread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadNotifications($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                is_following
                unread_count
                notifications {
                  is_read
                  follower { id }
                  message { preview }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(recipient),
        )
    )["record_thread"]
    assert unread == {
        "error_code": None,
        "is_following": False,
        "unread_count": 1,
        "notifications": [
            {
                "is_read": False,
                "follower": None,
                "message": {"preview": "Direct heads-up."},
            }
        ],
    }


def test_record_thread_returns_suggested_recipients(messaging_graphql_tables: None) -> None:
    """The record thread query exposes Odoo-style composer recipient suggestions."""

    poster = _platform_admin("msg-suggest-poster")
    assignee = _platform_admin("msg-suggest-assignee")
    recipient = _platform_admin("msg-suggest-recipient")
    follower = _platform_admin("msg-suggest-follower")
    with system_context(reason="test.messaging.record_suggested_recipients.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(
            title="Case 909",
            assigned_user=assignee,
        )
        ticket.message_subscribe(user=follower)
    with actor_context(poster):
        ticket.message_post(
            "Direct suggestion.",
            recipient_user_ids=(recipient.pk, follower.pk),
        )
    schema = _schema()

    suggestions = _data(
        execute_schema(
            schema,
            """
            query RecordThreadSuggestions($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                suggested_recipients {
                  reason
                  source
                  user {
                    username
                    email
                    is_active
                  }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(poster),
        )
    )["record_thread"]

    assert suggestions == {
        "error_code": None,
        "suggested_recipients": [
            {
                "reason": "Assigned user",
                "source": "assigned_user",
                "user": {
                    "username": "msg-suggest-assignee",
                    "email": "msg-suggest-assignee@example.com",
                    "is_active": True,
                },
            },
            {
                "reason": "Recent message recipient",
                "source": "recent_message_recipient",
                "user": {
                    "username": "msg-suggest-recipient",
                    "email": "msg-suggest-recipient@example.com",
                    "is_active": True,
                },
            },
        ],
    }


def test_record_chatter_reports_author_delivery_errors(messaging_graphql_tables: None) -> None:
    """The record chatter query reports Odoo-style delivery-error counters."""

    poster = _platform_admin("msg-error-poster")
    recipient = _platform_admin("msg-error-recipient")
    with system_context(reason="test.messaging.record_delivery_error.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 808")
    with actor_context(poster):
        message = ticket.message_post(
            "This delivery will fail.",
            recipient_user_ids=(recipient.pk,),
        )
    with system_context(reason="test.messaging.record_delivery_error.fail"):
        messaging_models.ThreadNotification.objects.mark_failed_for_message(
            message,
            user=recipient,
            status="bounce",
            failure_type="mail_bounce",
            failure_reason="Mailbox rejected the message.",
        )
    schema = _schema()

    payload = _data(
        execute_schema(
            schema,
            """
            query RecordThreadErrors($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                message_has_error
                message_has_error_counter
                notifications {
                  notification_status
                  failure_type
                  failure_reason
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(poster),
        )
    )["record_thread"]

    assert payload == {
        "error_code": None,
        "message_has_error": True,
        "message_has_error_counter": 1,
        "notifications": [],
    }


def test_record_chatter_post_with_attachment(messaging_graphql_tables: None, tmp_path: Path) -> None:
    """Posting a record chatter message can attach readable storage files."""

    admin = _platform_admin("msg-attachment-admin")
    with system_context(reason="test.messaging.record_attachment.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 404")
        _storage_drive(tmp_path, owner=admin)
        file = StorageFile.objects.ingest_bytes(b"Attachment body", filename="brief.txt", owner_id=admin.pk)
    schema = _schema()

    posted = _data(
        execute_schema(
            schema,
            """
            mutation PostRecordAttachment($model: String!, $id: ID!, $file: ID!) {
              post_record_message(
                input: {
                  model_label: $model
                  record_id: $id
                  body: "See attached."
                  attachment_ids: [$file]
                }
              ) {
                error_code
                attachment_count
                message {
                  preview
                  parts {
                    disposition
                    name
                    fragment { text }
                    file {
                      filename
                      size_bytes
                      mime_type { mime_type }
                    }
                  }
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid, "file": file.sqid},
            request=_request(admin),
        )
    )["post_record_message"]

    assert posted["error_code"] is None
    assert posted["attachment_count"] == 1
    assert posted["message"]["preview"] == "See attached."
    assert posted["message"]["parts"] == [
        {
            "disposition": "INLINE",
            "name": "",
            "fragment": {"text": "See attached."},
            "file": None,
        },
        {
            "disposition": "ATTACHMENT",
            "name": "brief.txt",
            "fragment": None,
            "file": {
                "filename": "brief.txt",
                "size_bytes": len(b"Attachment body"),
                "mime_type": {"mime_type": "text/plain"},
            },
        },
    ]


def test_record_chatter_follow_toggle(messaging_graphql_tables: None) -> None:
    """The custom record follower mutation mirrors Odoo's follow/unfollow contract."""

    admin = _platform_admin("msg-follow-admin")
    with system_context(reason="test.messaging.record_follow.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 202")
    schema = _schema()

    followed = _data(
        execute_schema(
            schema,
            """
            mutation Follow($model: String!, $id: ID!) {
              set_record_following(
                input: {
                  model_label: $model
                  record_id: $id
                  following: true
                  notification_policy: "email"
                  subtype_keys: ["comment", "activity"]
                }
              ) {
                error_code
                follower_count
                is_following
                follower {
                  notification_policy
                  subtype_keys
                  user { username }
                }
                thread { subject }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["set_record_following"]

    assert followed["error_code"] is None
    assert followed["follower_count"] == 1
    assert followed["is_following"] is True
    assert followed["thread"] == {"subject": "Case 202"}
    assert followed["follower"] == {
        "notification_policy": "EMAIL",
        "subtype_keys": ["comment", "activity"],
        "user": {"username": "msg-follow-admin"},
    }

    record_thread = _data(
        execute_schema(
            schema,
            """
            query RecordThreadFollowerOptions($model: String!, $id: ID!) {
              record_thread(input: {model_label: $model, record_id: $id}) {
                error_code
                self_follower {
                  notification_policy
                  subtype_keys
                  user { username }
                }
                subtypes {
                  key
                  name
                  description
                  default
                }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["record_thread"]

    assert record_thread["error_code"] is None
    assert record_thread["self_follower"] == followed["follower"]
    subtype_options = {
        option["key"]: option
        for option in record_thread["subtypes"]
    }
    assert subtype_options["comment"] == {
        "key": "comment",
        "name": "Comment",
        "description": "Discussion comment",
        "default": True,
    }
    assert subtype_options["activity_done"]["name"] == "Activity done"

    unfollowed = _data(
        execute_schema(
            schema,
            """
            mutation Unfollow($model: String!, $id: ID!) {
              set_record_following(
                input: {model_label: $model, record_id: $id, following: false}
              ) {
                error_code
                follower_count
                is_following
                follower { id }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["set_record_following"]

    assert unfollowed == {
        "error_code": None,
        "follower_count": 0,
        "is_following": False,
        "follower": None,
    }


def test_record_chatter_activity_lifecycle(messaging_graphql_tables: None) -> None:
    """The custom record activity mutations schedule and complete chatter activities."""

    admin = _platform_admin("msg-activity-admin")
    with system_context(reason="test.messaging.record_activity.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 303")
    schema = _schema()

    scheduled = _data(
        execute_schema(
            schema,
            """
            mutation Schedule($model: String!, $id: ID!) {
              schedule_record_activity(
                input: {
                  model_label: $model
                  record_id: $id
                  summary: "Call customer"
                  note: "Ask about rollout."
                  due_date: "2026-01-01"
                  activity_type: "call"
                }
              ) {
                error_code
                activity_count
                activity {
                  id
                  summary
                  note
                  due_date
                  activity_type
                  status
                  state
                  user { username }
                }
                activities {
                  summary
                  status
                }
                thread { subject }
              }
            }
            """,
            {"model": "messaging.ThreadedTicket", "id": ticket.sqid},
            request=_request(admin),
        )
    )["schedule_record_activity"]

    assert scheduled["error_code"] is None
    assert scheduled["activity_count"] == 1
    assert scheduled["thread"] == {"subject": "Case 303"}
    assert scheduled["activity"]["summary"] == "Call customer"
    assert scheduled["activity"]["note"] == "Ask about rollout."
    assert scheduled["activity"]["due_date"] == "2026-01-01"
    assert scheduled["activity"]["activity_type"] == "call"
    assert scheduled["activity"]["status"] == "TODO"
    assert scheduled["activity"]["user"] == {"username": "msg-activity-admin"}
    assert scheduled["activities"] == [{"summary": "Call customer", "status": "TODO"}]

    completed = _data(
        execute_schema(
            schema,
            """
            mutation Complete($activity: ID!) {
              complete_record_activity(
                input: {
                  activity_id: $activity
                  feedback: "Customer confirmed."
                }
              ) {
                error_code
                activity_count
                activity {
                  summary
                  status
                  state
                  feedback
                  completed_at
                }
                thread {
                  message_count
                  messages { preview }
                }
              }
            }
            """,
            {"activity": scheduled["activity"]["id"]},
            request=_request(admin),
        )
    )["complete_record_activity"]

    assert completed["error_code"] is None
    assert completed["activity_count"] == 1
    assert completed["activity"]["summary"] == "Call customer"
    assert completed["activity"]["status"] == "DONE"
    assert completed["activity"]["state"] == "done"
    assert completed["activity"]["feedback"] == "Customer confirmed."
    assert completed["activity"]["completed_at"] is not None
    assert completed["thread"] == {
        "message_count": 1,
        "messages": [{"preview": "Activity done: Call customer\n\nCustomer confirmed."}],
    }


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
def test_activity_agenda_bare_assignee_gets_pointer_not_parent(
    messaging_graphql_tables: None,
    storage: str,
) -> None:
    """The agenda hands a bare assignee its own activity + record pointer, never the parent (§3.8).

    ``activity_agenda`` is the first GraphQL surface delivering a ``ThreadActivity`` to an
    assignee who holds NO grant on the parent record: the activities are scheduled elevated
    (``created_by`` is the record owner, not the assignee), so the actor reaches its own
    rows only through the ``messaging/thread_activity.read`` ``user`` (assignee) arm. The
    projection must give the subject the activity's own fields, its own identity, and the
    minimal record pointer (label + model_label + record_id, ordered by due date across
    records) — and MUST NOT leak the parent thread (subject / message counts) or the
    attachment metadata, which a to-one traversal resolves unguarded through ``_base_manager``.
    That over-grant is closed structurally: the narrowed ``AgendaActivityType`` has no
    ``thread`` field and its ``attachment`` is a minimal pointer with no ``metadata``, so
    selecting either is a schema error. Verified in both REBAC storage modes — the
    registry-only translation is exercised alongside the bare denormalized default.
    """

    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=storage):
        owner = User.objects.create_user(username=f"agenda-owner-{storage}", email=f"ao-{storage}@example.com")
        assignee = User.objects.create_user(
            username=f"agenda-assignee-{storage}",
            email=f"aa-{storage}@example.com",
        )
        with actor_context(owner):
            alpha = messaging_models.ThreadedTicket.objects.create(title="Alpha")
            beta = messaging_models.ThreadedTicket.objects.create(title="Beta")
            beta.activity_schedule(user=assignee, summary="Call Beta", due_date=date(2026, 3, 10))
            alpha.activity_schedule(user=assignee, summary="Email Alpha", due_date=date(2026, 3, 5))
            # Another actor's assignment must never surface on this actor's agenda.
            alpha.activity_schedule(user=owner, summary="Owner task", due_date=date(2026, 3, 6))

        schema = _schema()
        window = {"start": "2026-03-01", "end": "2026-04-01"}
        rows = _data(
            execute_schema(
                schema,
                """
                query Agenda($start: Date!, $end: Date!) {
                  activity_agenda(window_start: $start, window_end: $end) {
                    summary
                    due_date
                    state
                    attachment { label model_label record_id }
                    user { username }
                  }
                }
                """,
                window,
                request=_request(assignee),
            )
        )["activity_agenda"]

        # The subject reads its own assignments across both records, due-date ordered, and
        # nothing of the owner's own task — the assignee arm never crosses to a non-assignee.
        assert [row["summary"] for row in rows] == ["Email Alpha", "Call Beta"]
        assert rows[0]["user"] == {"username": f"agenda-assignee-{storage}"}
        assert rows[0]["attachment"] == {
            "label": "Alpha",
            "model_label": "messaging.ThreadedTicket",
            "record_id": alpha.public_id,
        }
        assert rows[1]["attachment"] == {
            "label": "Beta",
            "model_label": "messaging.ThreadedTicket",
            "record_id": beta.public_id,
        }

        # §3.8 over-grant closed structurally: the parent thread and the attachment metadata
        # are not on the narrowed agenda type, so a bare assignee cannot read the record's
        # thread subject / message counts / attachment metadata through the agenda.
        leaked = execute_schema(
            schema,
            """
            query Leak($start: Date!, $end: Date!) {
              activity_agenda(window_start: $start, window_end: $end) {
                thread { subject message_count }
                attachment { metadata }
              }
            }
            """,
            window,
            request=_request(assignee),
        )

    assert leaked.errors is not None
    reasons = " ".join(str(error) for error in leaked.errors)
    assert "thread" in reasons
    assert "metadata" in reasons


@pytest.fixture()
def messaging_graphql_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete messaging GraphQL tables and sync REBAC."""

    del transactional_db
    created_models = _create_missing_tables(MESSAGING_GRAPHQL_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(MESSAGING_GRAPHQL_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _schema() -> Any:
    """Build the merged console schema used by the messaging app."""

    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema)
    ]
    return GraphQLSchemas(addons).build("console")


def _seed_thread_and_message(owner: Any) -> tuple[Any, Any]:
    """Create one readable/editable thread and message pair."""

    with system_context(reason="test.messaging.hasura.seed"):
        thread = messaging_models.Thread.objects.create(
            subject="Original",
            visibility="private",
            created_by_id=owner.pk,
        )
        message = messaging_models.Message.objects.create(
            thread=thread,
            subject="Original message",
            status="synced",
            sent_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            created_by_id=owner.pk,
        )
    return thread, message


def _storage_drive(tmp_path: Path, *, owner: Any) -> Any:
    """Create the default storage drive used by attachment tests."""

    backend = Backend._base_manager.create(
        slug="local",
        label="Local",
        backend_class="local",
        backend_config={"root": str(tmp_path), "base_url": "/media/"},
    )
    MimeType._base_manager.get_or_create(
        mime_type="text/plain",
        defaults={"category": "text", "label": "Text"},
    )
    MimeType._base_manager.get_or_create(
        mime_type="application/octet-stream",
        defaults={"category": "other", "label": "Binary file"},
    )
    return Drive._base_manager.create(
        backend=backend,
        slug="assets",
        name="Assets",
        prefix="assets",
        created_by=owner,
    )


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the universal admin role."""

    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin


def _request(user: Any) -> Any:
    """Return a console-shaped POST request bound to ``user``."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user
    return request


def _grant(resource: Any, relation: str, user: Any) -> None:
    """Write one direct relationship tuple for ``user`` on ``resource``."""

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation=relation,
                subject=to_subject_ref(user),
            )
        ]
    )


def test_generic_thread_and_message_lists_exclude_record_chatter(messaging_graphql_tables: None) -> None:
    """The generic threads/messages resources exclude record-attached chatter.

    F-v part 2: the owner-scoped ``threads``/``messages`` auto-CRUD resources are the
    channel inbox — list, aggregate, and by-pk. Record chatter (a thread bound to a
    record through a ``ThreadAttachment``) is reachable only through ``record_thread``
    (gated on the parent record's read), so it must not surface here, even for a
    platform admin who could otherwise read every owner-scoped row.
    """

    admin = _platform_admin("msg-inbox-admin")
    channel_thread, channel_message = _seed_thread_and_message(admin)
    with system_context(reason="test.messaging.inbox.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case 42")
    with actor_context(admin):
        record_message = ticket.message_post("Internal chatter")
    record_thread = record_message.thread
    schema = _schema()

    data = _data(
        execute_schema(
            schema,
            """
            query Inbox {
              threads { id }
              messages { id }
              threads_aggregate { aggregate { count } }
              messages_aggregate { aggregate { count } }
            }
            """,
            request=_request(admin),
        )
    )
    thread_ids = {row["id"] for row in data["threads"]}
    message_ids = {row["id"] for row in data["messages"]}
    assert channel_thread.sqid in thread_ids
    assert record_thread.sqid not in thread_ids
    assert channel_message.sqid in message_ids
    assert record_message.sqid not in message_ids
    # The aggregate source is scoped in lockstep with the list.
    assert data["threads_aggregate"]["aggregate"]["count"] == 1
    assert data["messages_aggregate"]["aggregate"]["count"] == 1

    by_pk = _data(
        execute_schema(
            schema,
            """
            query ByPk($thread: String!, $message: String!) {
              threads_by_pk(id: $thread) { id }
              messages_by_pk(id: $message) { id }
            }
            """,
            {"thread": record_thread.sqid, "message": record_message.sqid},
            request=_request(admin),
        )
    )
    # The by-pk route excludes the record thread/message too — not just the list.
    assert by_pk["threads_by_pk"] is None
    assert by_pk["messages_by_pk"] is None


def test_complete_and_cancel_activity_authorize_through_record_read(messaging_graphql_tables: None) -> None:
    """Complete/cancel reach the activity through the parent record's read.

    F-v part 3: a user who cannot read the parent record cannot complete or cancel an
    activity attached to it — the denial surfaces at the record read (``NOT_FOUND``),
    not the activity's own messaging permission, so an activity id alone never leaks a
    record's chatter. An authorized actor still completes it.
    """

    admin = _platform_admin("msg-act-admin")
    with system_context(reason="test.chatterdemo.part3.seed"):
        outsider = User.objects.create_user(username="cdc-outsider", email="cdc-outsider@example.com")
        doc = messaging_models.ChatterDoc.objects.create(title="Gated 1", status="open")
    schema = _schema()

    scheduled = _data(
        execute_schema(
            schema,
            """
            mutation Schedule($model: String!, $id: ID!) {
              schedule_record_activity(
                input: {model_label: $model, record_id: $id, summary: "Follow up", activity_type: "todo"}
              ) {
                error_code
                activity { id }
              }
            }
            """,
            {"model": "chatterdemo.ChatterDoc", "id": doc.sqid},
            request=_request(admin),
        )
    )["schedule_record_activity"]
    assert scheduled["error_code"] is None
    activity_id = scheduled["activity"]["id"]

    outsider_complete = _data(
        execute_schema(
            schema,
            """
            mutation Complete($activity: ID!) {
              complete_record_activity(input: {activity_id: $activity, feedback: "Sneaky"}) {
                error_code
                activity { status }
              }
            }
            """,
            {"activity": activity_id},
            request=_request(outsider),
        )
    )["complete_record_activity"]
    assert outsider_complete["error_code"] == "NOT_FOUND"
    assert outsider_complete["activity"] is None

    outsider_cancel = _data(
        execute_schema(
            schema,
            """
            mutation Cancel($activity: ID!) {
              cancel_record_activity(input: {activity_id: $activity}) {
                error_code
                activity { status }
              }
            }
            """,
            {"activity": activity_id},
            request=_request(outsider),
        )
    )["cancel_record_activity"]
    assert outsider_cancel["error_code"] == "NOT_FOUND"
    assert outsider_cancel["activity"] is None

    # The outsider changed nothing — the activity is still open.
    with system_context(reason="test.chatterdemo.part3.read"):
        thread = doc.message_thread(create=False)
        assert messaging_models.ThreadActivity._base_manager.get(thread=thread).status == "todo"

    completed = _data(
        execute_schema(
            schema,
            """
            mutation Complete($activity: ID!) {
              complete_record_activity(input: {activity_id: $activity, feedback: "Real"}) {
                error_code
                activity { status feedback }
              }
            }
            """,
            {"activity": activity_id},
            request=_request(admin),
        )
    )["complete_record_activity"]
    assert completed["error_code"] is None
    assert completed["activity"]["status"] == "DONE"
    assert completed["activity"]["feedback"] == "Real"


def test_generic_delete_excludes_record_thread_from_its_creator(messaging_graphql_tables: None) -> None:
    """A record thread is off the generic delete surface, even for its own creator.

    F-v part 2, write side: record chatter is reachable only through
    ``record_thread`` (gated on the parent record's read). The thread's own
    ``delete = owner + admin`` would let the creator who lost record access delete it
    through the generic ``delete_threads_by_pk``; the ``.inbox()`` write scope keeps
    it off that surface, so the by-pk delete cannot resolve a target. The same
    creator still deletes an ordinary inbox thread they own — the isolation is the
    gate, not a blanket denial.
    """

    creator = User.objects.create_user(username="thread-creator", email="tc@example.com")
    with system_context(reason="test.messaging.delete_isolation.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case D")
    with actor_context(creator):
        record_thread = ticket.message_post("Internal chatter").thread
        inbox_thread = messaging_models.Thread.objects.create(subject="Owned inbox", created_by_id=creator.pk)
    schema = _schema()

    delete = """
        mutation Delete($id: String!) {
          delete_threads_by_pk(id: $id) { id }
        }
    """
    record_result = execute_schema(schema, delete, {"id": record_thread.sqid}, request=_request(creator))
    # The record thread is not on the generic write surface: the by-pk lookup misses
    # it, so the delete resolves no target and reports the miss.
    assert record_result.errors is not None
    assert (record_result.data or {}).get("delete_threads_by_pk") is None

    inbox_deleted = _data(
        execute_schema(schema, delete, {"id": inbox_thread.sqid}, request=_request(creator))
    )["delete_threads_by_pk"]
    assert inbox_deleted == {"id": inbox_thread.sqid}

    with system_context(reason="test.messaging.delete_isolation.verify"):
        # The record thread survived; the inbox thread the creator owns did not.
        assert messaging_models.Thread._base_manager.filter(sqid=record_thread.sqid).exists()
        assert not messaging_models.Thread._base_manager.filter(sqid=inbox_thread.sqid).exists()


def test_record_writer_completes_activity_they_neither_own_nor_are_assigned(
    messaging_graphql_tables: None,
) -> None:
    """A record writer completes an activity on the record's authority alone (F-v §3.4).

    Completing rides the record's ``thread_activity_access`` (``write`` for
    ``ChatterDoc``), not the activity's own ``write`` (assignee/owner/thread-owner).
    A ``writer`` who is none of those still completes it: the manager elevates the
    activity save under ``system_context`` after the record preflight, so the
    activity's own permission never re-denies the record-authorized action.
    """

    admin = _platform_admin("msg-act-writer-admin")
    with system_context(reason="test.chatterdemo.writer.seed"):
        writer = User.objects.create_user(username="cdc-writer", email="cdc-writer@example.com")
        doc = messaging_models.ChatterDoc.objects.create(title="Writer gated", status="open")
    _grant(doc, "writer", writer)
    schema = _schema()

    scheduled = _data(
        execute_schema(
            schema,
            """
            mutation Schedule($model: String!, $id: ID!) {
              schedule_record_activity(
                input: {model_label: $model, record_id: $id, summary: "Follow up", activity_type: "todo"}
              ) {
                error_code
                activity { id }
              }
            }
            """,
            {"model": "chatterdemo.ChatterDoc", "id": doc.sqid},
            request=_request(admin),
        )
    )["schedule_record_activity"]
    assert scheduled["error_code"] is None
    activity_id = scheduled["activity"]["id"]

    # The writer is neither the assignee (admin), the activity/thread owner (admin),
    # nor an admin — only a record writer. It completes on the record's authority.
    completed = _data(
        execute_schema(
            schema,
            """
            mutation Complete($activity: ID!) {
              complete_record_activity(input: {activity_id: $activity, feedback: "By writer"}) {
                error_code
                activity { status feedback }
              }
            }
            """,
            {"activity": activity_id},
            request=_request(writer),
        )
    )["complete_record_activity"]
    assert completed["error_code"] is None
    assert completed["activity"]["status"] == "DONE"
    assert completed["activity"]["feedback"] == "By writer"


def test_record_chatter_rows_opt_out_of_change_broadcasts(messaging_graphql_tables: None) -> None:
    """Record chatter rows never broadcast on the generic ``changes`` subscription.

    F-v part 2, subscription side: a record-attached thread/message returns
    ``broadcasts_changes() == False``, so the publisher drops its create/update/delete
    at emission — it is never delivered to a subject who cannot read the record (the
    thread/message's own ``owner``/``admin`` read would otherwise deliver it). Channel
    inbox rows, and a message whose thread merged away, still broadcast.
    """

    admin = _platform_admin("msg-changes-admin")
    channel_thread, channel_message = _seed_thread_and_message(admin)
    with system_context(reason="test.messaging.changes.seed"):
        ticket = messaging_models.ThreadedTicket.objects.create(title="Case C")
    with actor_context(admin):
        record_message = ticket.message_post("Internal chatter")
    record_thread = record_message.thread

    with system_context(reason="test.messaging.changes.verify"):
        orphan = messaging_models.Message.objects.create(
            subject="Orphan", status="synced", created_by_id=admin.pk
        )
        # Channel inbox rows broadcast; record-attached chatter does not.
        assert channel_thread.broadcasts_changes() is True
        assert channel_message.broadcasts_changes() is True
        assert record_thread.broadcasts_changes() is False
        assert record_message.broadcasts_changes() is False
        # A message with no thread is not record-attached and stays on the surface.
        assert orphan.broadcasts_changes() is True
