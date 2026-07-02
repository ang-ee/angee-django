"""Tests for ``changes`` model-change subscriptions and REBAC gating."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
import strawberry
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ImproperlyConfigured
from django.db.models.signals import post_save
from django.test import override_settings
from rebac import actor_context, anonymous_actor, current_actor
from rebac.graphql.strawberry import RebacChannelsConsumerMixin

from angee.graphql import publishing, subscriptions
from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.events import ChangeEvent, ChangePayload
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.subscriptions import changes
from tests.conftest import SchemaAddon

ANON = anonymous_actor()
SubscriptionResolver = Callable[
    [object, object],
    AsyncGenerator[ChangeEvent, None],
]


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "auth.Group",
        "id": "1",
        "action": "update",
        "changed_fields": ["name"],
        "changed_values": {"name": "x"},
    }
    payload.update(overrides)
    return payload


def _subscription_resolver(surface: type) -> SubscriptionResolver:
    """Return the generated subscription resolver from ``surface``."""

    definition = getattr(surface, "__strawberry_definition__")
    field = definition.fields[0]
    return cast(SubscriptionResolver, field.base_resolver.wrapped_func)


def _receiver_count(signal: Any, dispatch_uid: str) -> int:
    """Return receivers connected with ``dispatch_uid``."""

    return sum(1 for receiver in signal.receivers if receiver[0][0] == dispatch_uid)


def test_changes_builds_a_named_subscription_field() -> None:
    """``changes`` exposes one subscription field without wiring publishers."""

    receiver_count = _receiver_count(post_save, "angee-changes-auth.Group-save")
    surface = changes(Group, field="groupChanged")

    @strawberry.type
    class Query:
        @strawberry.field
        def ok(self) -> bool:
            return True

    sdl = strawberry.Schema(query=Query, subscription=surface).as_str()
    assert "groupChanged: ChangeEvent!" in sdl
    assert _receiver_count(post_save, "angee-changes-auth.Group-save") == receiver_count


def test_schema_build_wires_change_publishers_from_subscription_metadata() -> None:
    """Building a schema connects publishers for declared ``changes`` resources."""

    dispatch_uid = "angee-changes-auth.Group-save"
    was_connected = _receiver_count(post_save, dispatch_uid) > 0
    post_save.disconnect(sender=Group, dispatch_uid=dispatch_uid)

    @strawberry.type
    class Query:
        @strawberry.field
        def ok(self) -> bool:
            return True

    try:
        surface = changes(Group, field="groupChanged")
        GraphQLSchemas(
            [
                SchemaAddon(
                    {
                        "public": {
                            "query": (Query,),
                            "subscription": (surface,),
                        }
                    }
                )
            ]
        ).build("public")
        assert _receiver_count(post_save, dispatch_uid) == 1
    finally:
        if was_connected:
            publishing.connect_publishers(Group)
        else:
            post_save.disconnect(sender=Group, dispatch_uid=dispatch_uid)


def test_subscribe_yields_broadcast_payloads(monkeypatch) -> None:
    """A payload sent to the model group reaches an active subscriber."""

    layer = InMemoryChannelLayer()
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: layer)

    async def scenario() -> ChangePayload:
        stream = subscriptions._subscribe(Group)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)  # let the subscriber join the group
        await layer.group_send(
            publishing.change_group(Group),
            {"type": "angee.change", "payload": _payload(id="7")},
        )
        try:
            return await asyncio.wait_for(pending, timeout=1)
        finally:
            await stream.aclose()

    payload = asyncio.run(scenario())
    assert payload.id == "7"


def test_subscription_resolver_gates_events_through_sync_adapter(
    monkeypatch,
) -> None:
    """Gate construction and filtering do not use the shared thread-sensitive executor."""

    closed_connections: list[str] = []
    thread_flags: list[bool] = []

    async def subscribe(model: type[Group]):
        """Yield one payload without touching the channel layer."""

        assert model is Group
        yield _payload(id="9")

    class Gate:
        """Gate stub that returns a visible event for any payload."""

        def __init__(self, model: type[Group], actor: object) -> None:
            assert model is Group
            assert actor == ANON

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            return ChangeEvent.from_payload(payload)

    def sync_adapter(
        func: Callable[..., object],
        *,
        thread_sensitive: bool,
    ) -> Callable[..., Awaitable[object]]:
        """Record each offloaded call's thread-sensitivity, then run it."""

        thread_flags.append(thread_sensitive)

        async def wrapper(*args: object) -> object:
            return func(*args)

        return wrapper

    monkeypatch.setattr(subscriptions, "_subscribe", subscribe)
    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)
    monkeypatch.setattr(
        subscriptions,
        "close_old_connections",
        lambda: closed_connections.append("close"),
        raising=False,
    )
    monkeypatch.setattr(
        subscriptions,
        "sync_to_async",
        sync_adapter,
        raising=False,
    )
    surface = changes(Group, field="groupChanged")
    resolver = _subscription_resolver(surface)

    async def scenario() -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        info = SimpleNamespace(context={"actor": ANON})
        with actor_context(ANON):
            async for event in resolver(object(), info):
                events.append(event)
        return events

    events = asyncio.run(scenario())

    # One gate construction + one payload filter, both on the general sync executor.
    assert thread_flags == [False, False]
    assert closed_connections == ["close", "close", "close", "close"]
    assert [event.id for event in events] == [strawberry.ID("9")]


def test_subscription_resolver_uses_current_actor(monkeypatch) -> None:
    """Subscription gating reads the ambient REBAC actor."""

    seen: list[object] = []

    async def subscribe(model: type[Group]):
        """Yield one payload without touching the channel layer."""

        assert model is Group
        yield _payload(id="10")

    class Gate:
        """Gate stub that records the actor it gates for."""

        def __init__(self, model: type[Group], actor: object) -> None:
            assert model is Group
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            seen.append((self.actor, current_actor()))
            return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "_subscribe", subscribe)
    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)
    surface = changes(Group, field="groupChanged")
    resolver = _subscription_resolver(surface)

    async def scenario() -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        info = SimpleNamespace(context={})
        with actor_context(ANON):
            async for event in resolver(object(), info):
                events.append(event)
        return events

    events = asyncio.run(scenario())

    assert seen == [(ANON, ANON)]
    assert [event.id for event in events] == [strawberry.ID("10")]


def test_subscription_resolver_denies_without_current_actor(
    monkeypatch,
) -> None:
    """A subscription with no ambient actor yields no change events."""

    calls: list[object] = []

    async def subscribe(model: type[Group]):
        """Yield one payload without touching the channel layer."""

        assert model is Group
        yield _payload(id="11")

    class Gate:
        """Gate stub that fails the test if no-actor subscriptions reach it."""

        def __init__(self, model: type[Group], actor: object) -> None:
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            calls.append(payload)
            return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "_subscribe", subscribe)
    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)
    surface = changes(Group, field="groupChanged")
    resolver = _subscription_resolver(surface)

    async def scenario() -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        info = SimpleNamespace(context={})
        async for event in resolver(object(), info):
            events.append(event)
        return events

    events = asyncio.run(scenario())

    assert events == []
    assert calls == []


@override_settings(
    DEBUG=False,
    ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER=False,
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
def test_inmemory_channel_layer_requires_dev_or_explicit_opt_in() -> None:
    """The default in-memory changes transport is loud outside dev/test."""

    with pytest.raises(ImproperlyConfigured, match="dev-only"):
        publishing.change_channel_layer()


def test_ws_consumer_uses_rebac_channels_mixin() -> None:
    """The GraphQL WS consumer delegates actor setup to REBAC."""

    assert issubclass(AngeeGraphQLWSConsumer, RebacChannelsConsumerMixin)


def test_ws_query_runs_inside_actor_context() -> None:
    """GraphQL-over-WebSocket queries install the connection actor."""

    @strawberry.type
    class Query:
        """Minimal query root for the consumer."""

        @strawberry.field
        def actor_active(self) -> bool:
            """Return whether the ambient actor is installed."""

            return current_actor() == ANON

    async def scenario() -> dict[str, object]:
        application = AngeeGraphQLWSConsumer.as_asgi(schema=strawberry.Schema(query=Query))
        communicator = WebsocketCommunicator(
            application,
            "/graphql",
            subprotocols=["graphql-transport-ws"],
        )
        communicator.scope["user"] = AnonymousUser()
        connected, _subprotocol = await communicator.connect()
        assert connected
        await communicator.send_json_to({"type": "connection_init"})
        assert await communicator.receive_json_from() == {"type": "connection_ack"}
        await communicator.send_json_to(
            {
                "id": "1",
                "type": "subscribe",
                "payload": {"query": "{ actorActive }"},
            }
        )
        payload = await communicator.receive_json_from()
        assert await communicator.receive_json_from() == {
            "id": "1",
            "type": "complete",
        }
        await communicator.disconnect()
        return payload

    result = asyncio.run(scenario())

    assert result == {
        "id": "1",
        "type": "next",
        "payload": {"data": {"actorActive": True}},
    }
    assert current_actor() is None
