"""Model change subscriptions delivered over the channel layer.

``changes(Model, field=...)`` returns a Strawberry subscription surface that
streams a :class:`ChangeEvent` whenever an instance is created, updated, or
deleted. Saves and deletes are published to a per-model channel-layer group by
``angee.base.signals``; each subscriber drains its own channel and yields
events.

Events are REBAC read-gated at emit time: a subscriber only sees changes to
rows it may read, with field-gated values redacted. Models without a REBAC
resource type stream unfiltered. The WebSocket transport resolves the
connection actor in ``angee.base.consumers``; this module reads it from the
context.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from types import SimpleNamespace
from typing import Any

import strawberry
from channels.layers import get_channel_layer
from django.db import models
from rebac import ObjectRef, SubjectRef, anonymous_actor
from rebac.actors import get_actor_resolver
from rebac.backends import backend
from rebac.field_visibility import check_field_access, gated_read_fields
from rebac.resources import model_resource_type
from strawberry.scalars import JSON

from angee.base.signals import change_group, connect_publishers


@strawberry.type
class ChangeEvent:
    """A read-gated notification that one model instance changed."""

    model: str
    id: strawberry.ID
    action: str
    changed_fields: list[str] | None = None
    changed_values: JSON | None = None


def changes(model: type[models.Model], *, field: str) -> type:
    """Return a subscription surface streaming changes to ``model``.

    The GraphQL field is named ``field``. Calling this also connects the change
    publishers for ``model`` (idempotently), so a schema that exposes the field
    is wired end to end.
    """

    connect_publishers(model)
    label = model._meta.label

    async def resolve(
        self: object, info: strawberry.Info
    ) -> AsyncGenerator[ChangeEvent, None]:
        actor = _actor_from_info(info)
        async for payload in _subscribe(model):
            event = _gate_event(model, actor, payload)
            if event is not None:
                yield event

    resolve.__name__ = field
    namespace = {field: strawberry.subscription(resolver=resolve, name=field)}
    surface = type(f"{model.__name__}Subscription", (), namespace)
    surface.__doc__ = f"Live changes to {label}."
    return strawberry.type(surface)


# --- subscribing ----------------------------------------------------------


async def _subscribe(
    model: type[models.Model],
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield change payloads for ``model`` until the subscriber disconnects."""

    layer = get_channel_layer()
    if layer is None:
        return
    group = change_group(model)
    channel = await layer.new_channel()
    await layer.group_add(group, channel)
    try:
        while True:
            message = await layer.receive(channel)
            payload = message.get("payload")
            if payload:
                yield payload
    finally:
        await layer.group_discard(group, channel)


# --- REBAC gating ---------------------------------------------------------


def _gate_event(
    model: type[models.Model], actor: SubjectRef, payload: Mapping[str, Any]
) -> ChangeEvent | None:
    """Drop events the actor cannot read and redact field-gated values."""

    resource_type = model_resource_type(model)
    if not resource_type:
        return _to_event(payload)

    resource = ObjectRef(resource_type, str(payload["id"]))
    active_backend = backend()
    if not check_field_access(
        active_backend, subject=actor, action="read", resource=resource
    ).allowed:
        return None
    return _to_event(_redact(model, actor, resource, payload, active_backend))


def _redact(
    model: type[models.Model],
    actor: SubjectRef,
    resource: ObjectRef,
    payload: Mapping[str, Any],
    active_backend: Any,
) -> dict[str, Any]:
    """Return a payload copy with unreadable field-gated values removed."""

    gated = gated_read_fields(model)
    fields = payload.get("changed_fields")
    values = payload.get("changed_values")
    if not gated or not fields:
        return dict(payload)

    denied = {
        name
        for name in fields
        if name in gated
        and not check_field_access(
            active_backend,
            subject=actor,
            action=f"read__{name}",
            resource=resource,
        ).allowed
    }
    if not denied:
        return dict(payload)

    redacted = dict(payload)
    redacted["changed_fields"] = [
        name for name in fields if name not in denied
    ]
    if isinstance(values, Mapping):
        redacted["changed_values"] = {
            name: value for name, value in values.items() if name not in denied
        }
    return redacted


def _to_event(payload: Mapping[str, Any]) -> ChangeEvent:
    """Build the GraphQL event from a change payload."""

    return ChangeEvent(
        model=str(payload["model"]),
        id=strawberry.ID(str(payload["id"])),
        action=str(payload["action"]),
        changed_fields=payload.get("changed_fields"),
        changed_values=payload.get("changed_values"),
    )


# --- actor resolution -----------------------------------------------------


def scope_actor(scope: Mapping[str, Any]) -> SubjectRef:
    """Resolve the REBAC actor for a channels connection scope."""

    request = SimpleNamespace(user=scope.get("user"))
    return get_actor_resolver()(request) or anonymous_actor()


def _actor_from_info(info: strawberry.Info) -> SubjectRef:
    """Return the connection actor attached to the GraphQL context."""

    context = info.context
    actor = (
        context.get("actor")
        if isinstance(context, Mapping)
        else getattr(context, "actor", None)
    )
    return actor or anonymous_actor()
