"""GraphQL subscription surfaces for model change events."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Mapping
from typing import Any

import strawberry
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import models
from rebac import SubjectRef, actor_context, anonymous_actor

from angee.base.access import ChangeReadGate
from angee.base.graphql.events import ChangeEvent
from angee.base.signals import change_group, connect_publishers


def changes(model: type[models.Model], *, field: str) -> type:
    """Return a subscription surface streaming changes to ``model``."""

    connect_publishers(model)
    label = model._meta.label

    async def resolve(
        self: object,
        info: strawberry.Info,
    ) -> AsyncGenerator[ChangeEvent, None]:
        """Yield read-gated change events for one subscriber."""

        del self
        actor = _actor_from_info(info)
        with actor_context(actor):
            async for payload in _subscribe(model):
                event = await sync_to_async(
                    _gate_event,
                    thread_sensitive=True,
                )(model, actor, payload)
                if event is not None:
                    yield event

    resolve.__name__ = field
    namespace = {field: strawberry.subscription(resolver=resolve, name=field)}
    surface = type(f"{model.__name__}Subscription", (), namespace)
    surface.__doc__ = f"Live changes to {label}."
    return strawberry.type(surface)


async def _subscribe(
    model: type[models.Model],
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield raw change payloads for ``model`` from the channel layer."""

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


def _gate_event(
    model: type[models.Model],
    actor: SubjectRef,
    payload: Mapping[str, Any],
) -> ChangeEvent | None:
    """Return a read-gated event for ``payload``."""

    filtered = ChangeReadGate(model, actor).filter(payload)
    if filtered is None:
        return None
    if isinstance(filtered, ChangeEvent):
        return filtered
    return ChangeEvent.from_payload(filtered)


def _actor_from_info(info: strawberry.Info) -> SubjectRef:
    """Return the connection actor attached to GraphQL context."""

    context = info.context
    actor = (
        context.get("actor")
        if isinstance(context, Mapping)
        else getattr(context, "actor", None)
    )
    return actor or anonymous_actor()
