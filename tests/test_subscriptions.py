"""Tests for ``changes`` model-change subscriptions and REBAC gating."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import strawberry
from channels.layers import InMemoryChannelLayer
from django.contrib.auth.models import Group
from rebac import anonymous_actor, current_actor
from strawberry.channels import GraphQLWSConsumer

from angee.base import signals
from angee.base.consumers import AngeeGraphQLWSConsumer, scope_actor
from angee.base.graphql import subscriptions
from angee.base.graphql.events import ChangeEvent
from angee.base.graphql.subscriptions import changes

ANON = anonymous_actor()


def _payload(**overrides: object) -> dict[str, object]:
    payload = {
        "model": "auth.Group",
        "id": "1",
        "action": "update",
        "changed_fields": ["name"],
        "changed_values": {"name": "x"},
    }
    payload.update(overrides)
    return payload


def test_changes_builds_a_named_subscription_field() -> None:
    """``changes`` exposes one subscription field and wires publishers."""

    surface = changes(Group, field="groupChanged")

    @strawberry.type
    class Query:
        @strawberry.field
        def ok(self) -> bool:
            return True

    sdl = strawberry.Schema(query=Query, subscription=surface).as_str()
    assert "groupChanged: ChangeEvent!" in sdl
    assert Group in signals._connected


def test_gate_event_passes_through_non_rebac_models(monkeypatch) -> None:
    """A model without a REBAC resource type streams unfiltered."""

    class Gate:
        """Gate stub that returns the payload unchanged."""

        def __init__(self, model: object, actor: object) -> None:
            self.model = model
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)

    event = subscriptions._gate_event(Group, ANON, _payload())

    assert isinstance(event, ChangeEvent)
    assert event.action == "update"
    assert event.changed_fields == ["name"]


def test_gate_event_drops_unreadable_rows(monkeypatch) -> None:
    """An event for a row the actor cannot read is suppressed."""

    class Gate:
        """Gate stub that drops every payload."""

        def __init__(self, model: object, actor: object) -> None:
            self.model = model
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> None:
            return None

    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)

    assert subscriptions._gate_event(Group, ANON, _payload()) is None


def test_gate_event_redacts_denied_fields(monkeypatch) -> None:
    """Field-gated values the actor cannot read are removed from the event."""

    class Gate:
        """Gate stub that redacts the secret field."""

        def __init__(self, model: object, actor: object) -> None:
            self.model = model
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            redacted = dict(payload)
            redacted["changed_fields"] = ["name"]
            redacted["changed_values"] = {"name": "x"}
            return ChangeEvent.from_payload(redacted)

    monkeypatch.setattr(subscriptions, "ChangeReadGate", Gate)

    event = subscriptions._gate_event(
        Group,
        ANON,
        _payload(
            changed_fields=["name", "secret"],
            changed_values={"name": "x", "secret": "y"},
        ),
    )

    assert event is not None
    assert event.changed_fields == ["name"]
    assert event.changed_values == {"name": "x"}


def test_subscribe_yields_broadcast_payloads(monkeypatch) -> None:
    """A payload sent to the model group reaches an active subscriber."""

    layer = InMemoryChannelLayer()
    monkeypatch.setattr(subscriptions, "get_channel_layer", lambda: layer)

    async def scenario() -> dict[str, object]:
        stream = subscriptions._subscribe(Group)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.05)  # let the subscriber join the group
        await layer.group_send(
            signals.change_group(Group),
            {"type": "angee.change", "payload": _payload(id="7")},
        )
        try:
            return await asyncio.wait_for(pending, timeout=1)
        finally:
            await stream.aclose()

    payload = asyncio.run(scenario())
    assert payload["id"] == "7"


def test_subscription_resolver_gates_events_through_sync_adapter(
    monkeypatch,
) -> None:
    """Subscription gating runs through the sync adapter."""

    calls: list[bool] = []

    async def subscribe(model: type[Group]):
        """Yield one payload without touching the channel layer."""

        assert model is Group
        yield _payload(id="9")

    def gate_event(
        model: type[Group],
        actor: object,
        payload: dict[str, object],
    ) -> ChangeEvent:
        """Return a visible event."""

        assert model is Group
        assert actor == ANON
        return ChangeEvent.from_payload(payload)

    def sync_adapter(func, *, thread_sensitive: bool):
        """Record that the resolver offloads the sync gate."""

        assert func is gate_event
        calls.append(thread_sensitive)

        async def wrapper(*args: object) -> object:
            return func(*args)

        return wrapper

    monkeypatch.setattr(subscriptions, "_subscribe", subscribe)
    monkeypatch.setattr(subscriptions, "_gate_event", gate_event)
    monkeypatch.setattr(
        subscriptions,
        "sync_to_async",
        sync_adapter,
        raising=False,
    )
    surface = changes(Group, field="groupChanged")
    field = surface.__strawberry_definition__.fields[0]
    resolver = field.base_resolver.wrapped_func

    async def scenario() -> list[ChangeEvent]:
        events: list[ChangeEvent] = []
        info = SimpleNamespace(context={"actor": ANON})
        async for event in resolver(object(), info):
            events.append(event)
        return events

    events = asyncio.run(scenario())

    assert calls == [True]
    assert [event.id for event in events] == [strawberry.ID("9")]


def test_ws_consumer_executes_operations_inside_actor_context(
    monkeypatch,
) -> None:
    """WebSocket GraphQL operations install the connection actor."""

    seen: list[object] = []

    async def execute(
        self: GraphQLWSConsumer,
        request: object,
        context: dict[str, object],
        root_value: object,
        sub_response: object,
    ) -> str:
        """Record the ambient actor while the operation executes."""

        del self, request, context, root_value, sub_response
        seen.append(current_actor())
        return "ok"

    monkeypatch.setattr(GraphQLWSConsumer, "execute_operation", execute)
    @strawberry.type
    class Query:
        """Minimal query root for the consumer."""

        @strawberry.field
        def ok(self) -> bool:
            """Return true."""

            return True

    consumer = AngeeGraphQLWSConsumer(strawberry.Schema(query=Query))
    consumer.scope = {}

    result = asyncio.run(
        consumer.execute_operation(
            object(),
            {"actor": ANON},
            None,
            object(),
        )
    )

    assert result == "ok"
    assert seen == [ANON]
    assert current_actor() is None


def test_scope_actor_defaults_to_anonymous() -> None:
    """A scope without an authenticated user resolves to anonymous."""

    assert scope_actor({}) == anonymous_actor()
