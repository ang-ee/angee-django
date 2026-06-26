"""Tests for the messaging GraphQL Hasura resources."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.messaging.models import Channel as AbstractChannel
from angee.messaging.models import MessageMetrics as AbstractMessageMetrics
from angee.messaging.models import Reaction as AbstractReaction
from tests import test_messaging as messaging_models
from tests import test_parties_graphql as parties_graphql
from tests.conftest import (
    Integration,
    SchemaAddon,
    _create_missing_tables,
    execute_schema,
)
from tests.conftest import result_data as _data

_ChannelMeta = getattr(AbstractChannel, "Meta", object)
_ReactionMeta = getattr(AbstractReaction, "Meta", object)
_MessageMetricsMeta = getattr(AbstractMessageMetrics, "Meta", object)


class Channel(Integration, AbstractChannel):
    """Concrete message channel used to import the messaging schema."""

    class Meta(_ChannelMeta):
        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_channel"
        rebac_resource_type = "messaging/channel"
        rebac_id_attr = "sqid"


class Reaction(AbstractReaction):
    """Concrete reaction model used to import the messaging schema."""

    class Meta(_ReactionMeta):
        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_reaction"
        rebac_resource_type = "messaging/reaction"
        rebac_id_attr = "sqid"


class MessageMetrics(AbstractMessageMetrics):
    """Concrete message metrics model used to import the messaging schema."""

    class Meta(_MessageMetricsMeta):
        abstract = False
        app_label = "messaging"
        db_table = "test_messaging_message_metrics"
        rebac_resource_type = "messaging/message_metrics"
        rebac_id_attr = "sqid"


messaging_schema = importlib.import_module("angee.messaging.schema")
iam_schema = importlib.import_module("angee.iam.schema")
integrate_schema = importlib.import_module("angee.integrate.schema")
parties_schema = parties_graphql.parties_schema
User = get_user_model()

MESSAGING_GRAPHQL_MODELS = (
    *messaging_models.MESSAGING_TEST_MODELS,
    Channel,
    Reaction,
    MessageMetrics,
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
        "platform",
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
        "platform",
        "sent_at",
    ]
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


@pytest.fixture()
def messaging_graphql_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete messaging GraphQL tables and sync REBAC."""

    del transactional_db
    created_models = _create_missing_tables(MESSAGING_GRAPHQL_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
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
