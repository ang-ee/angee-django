"""Tests for ``changes`` model-change subscriptions and REBAC gating."""

from __future__ import annotations

import asyncio
import datetime
from types import SimpleNamespace

import strawberry
from channels.layers import InMemoryChannelLayer
from django.contrib.auth.models import Group
from rebac import anonymous_actor

from angee.base import signals
from angee.base.graphql import subscriptions
from angee.base.graphql.subscriptions import ChangeEvent, changes

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

    monkeypatch.setattr(subscriptions, "model_resource_type", lambda model: "")

    event = subscriptions._gate_event(Group, ANON, _payload())

    assert isinstance(event, ChangeEvent)
    assert event.action == "update"
    assert event.changed_fields == ["name"]


def test_gate_event_drops_unreadable_rows(monkeypatch) -> None:
    """An event for a row the actor cannot read is suppressed."""

    monkeypatch.setattr(
        subscriptions, "model_resource_type", lambda model: "group"
    )
    monkeypatch.setattr(subscriptions, "backend", lambda: object())
    monkeypatch.setattr(
        subscriptions,
        "check_field_access",
        lambda *a, **k: SimpleNamespace(allowed=False),
    )

    assert subscriptions._gate_event(Group, ANON, _payload()) is None


def test_gate_event_redacts_denied_fields(monkeypatch) -> None:
    """Field-gated values the actor cannot read are removed from the event."""

    monkeypatch.setattr(
        subscriptions, "model_resource_type", lambda model: "group"
    )
    monkeypatch.setattr(subscriptions, "backend", lambda: object())
    monkeypatch.setattr(
        subscriptions, "gated_read_fields", lambda model: frozenset({"secret"})
    )

    def check(_backend, *, subject, action, resource):
        return SimpleNamespace(allowed=action != "read__secret")

    monkeypatch.setattr(subscriptions, "check_field_access", check)

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


def test_scope_actor_defaults_to_anonymous() -> None:
    """A scope without an authenticated user resolves to anonymous."""

    assert subscriptions.scope_actor({}) == anonymous_actor()


def test_json_safe_normalizes_values() -> None:
    """Non-primitive values become JSON-serializable representations."""

    when = datetime.datetime(2026, 5, 30, 12, 0, 0)
    assert signals._json_safe(when) == "2026-05-30T12:00:00"
    assert signals._json_safe([when]) == ["2026-05-30T12:00:00"]
    assert signals._json_safe(3) == 3
