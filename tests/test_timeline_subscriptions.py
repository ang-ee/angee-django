"""Timeline transport is separate from record feeds and rechecks read access."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.db import transaction
from rebac import (
    RelationshipTuple,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.models.identity import public_id_of
from angee.base.refs import RecordRef
from angee.graphql import publishing, subscriptions
from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.sdl import GraphQLSdl
from angee.graphql.testing import capture_broadcasts
from angee.timeline import schema as timeline_schema
from angee.timeline.events import broadcast_timeline_changes, timeline_group
from tests.conftest import SchemaAddon, create_user, vault_for

pytestmark = pytest.mark.django_db(transaction=True)


def test_timeline_publisher_never_emits_record_updates_and_discards_rollbacks(composed_tables):
    record = vault_for(create_user("timeline-publisher"))
    with capture_broadcasts() as records, patch("angee.timeline.events.broadcast_group") as timelines:
        with transaction.atomic():
            broadcast_timeline_changes([record, record])
            timelines.assert_not_called()
        timelines.assert_called_once_with(timeline_group(RecordRef.from_instance(record)), {
            "model": record._meta.label, "id": public_id_of(record),
        })
        timelines.reset_mock()
        with transaction.atomic():
            broadcast_timeline_changes([record])
            transaction.set_rollback(True)
        timelines.assert_not_called()
    assert records == []


@pytest.mark.parametrize("admitted", [False, True])
def test_timeline_subscription_authorizes_admission_and_each_delivery(
    composed_tables, monkeypatch, tmp_path, admitted,
):
    owner, reader = create_user("timeline-stream-owner"), create_user("timeline-stream-reader")
    record = vault_for(owner)
    grant = RelationshipTuple(to_object_ref(record), "viewer", to_subject_ref(reader))
    if admitted:
        with system_context(reason="test.timeline.admit"):
            write_relationships([grant])
    schemas = GraphQLSchemas([SchemaAddon(timeline_schema.schemas)])
    # Exercise the same SDL owner compose uses, without touching a running host.
    rendered = GraphQLSdl(schemas, schema_dir=tmp_path).render()["console"]
    assert "timelineChanged(model: String!, id: ID!): Boolean!" in rendered
    (tmp_path / "console.graphql").write_text(rendered)
    layer = InMemoryChannelLayer()
    joined = asyncio.Event()
    group_add = layer.group_add

    async def join(group, channel):
        await group_add(group, channel)
        joined.set()

    monkeypatch.setattr(layer, "group_add", join)
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: layer)
    monkeypatch.setattr(publishing, "change_channel_layer", lambda: layer)

    def publish(revoke=False):
        if revoke:
            with system_context(reason="test.timeline.revoke"):
                delete_relationship(grant)
        broadcast_timeline_changes([record])

    async def scenario():
        timeout = 10  # bounds a hang, not latency: a long-lived worker's full GC stops the loop over a second
        client = WebsocketCommunicator(
            AngeeGraphQLWSConsumer.as_asgi(schema=schemas.build("console")),
            "/graphql/console/", subprotocols=["graphql-transport-ws"],
        )
        client.scope["user"] = reader
        assert (await client.connect(timeout))[0]
        try:
            await client.send_json_to({"type": "connection_init"})
            assert await client.receive_json_from(timeout) == {"type": "connection_ack"}
            await client.send_json_to({"id": "timeline", "type": "subscribe", "payload": {
                "query": "subscription($model: String!, $id: ID!) { timelineChanged(model: $model, id: $id) }",
                "variables": {"model": record._meta.label, "id": public_id_of(record)},
            }})
            if admitted:
                await asyncio.wait_for(joined.wait(), timeout)
                await sync_to_async(publish)()
                assert await client.receive_json_from(timeout) == {
                    "id": "timeline", "type": "next", "payload": {"data": {"timelineChanged": True}},
                }
                await sync_to_async(publish)(revoke=True)
            assert await client.receive_json_from(timeout) == {"id": "timeline", "type": "complete"}
            assert not layer.groups
        finally:
            await client.disconnect()

    asyncio.run(scenario())
