"""GraphQL subscription surfaces for model change events."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import strawberry
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import models
from rebac import current_actor

from angee.graphql.access import ChangeReadGate
from angee.graphql.data.metadata import (
    DataResourceRoots,
    DataResourceTypeNames,
    attach_data_resource_metadata,
    make_data_resource_metadata,
    resource_wire_field_name,
)
from angee.graphql.events import ChangeEvent, ChangePayload
from angee.graphql.publishing import change_group, connect_publishers


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
        del info
        actor = current_actor()
        if actor is None:
            return
        # The gate resolves this model+actor's authorization facts once; each
        # payload is filtered (and redacted) through it in the sync thread.
        gate = await sync_to_async(ChangeReadGate, thread_sensitive=True)(model, actor)
        async for payload in _subscribe(model):
            event = await sync_to_async(gate.filter, thread_sensitive=True)(payload)
            if event is not None:
                yield event

    resolve.__name__ = field
    namespace = {field: strawberry.subscription(resolver=resolve, name=field)}
    surface = type(f"{model.__name__}Subscription", (), namespace)
    surface.__doc__ = f"Live changes to {label}."
    surface = strawberry.type(surface)
    return attach_data_resource_metadata(
        surface,
        make_data_resource_metadata(
            model=model,
            roots=DataResourceRoots(changes_name=resource_wire_field_name(surface, field)),
            type_names=DataResourceTypeNames(),
            capabilities=("changes",),
        ),
    )


async def _subscribe(
    model: type[models.Model],
) -> AsyncGenerator[ChangePayload, None]:
    """Yield change payloads for ``model`` from the channel layer."""

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
                yield ChangePayload.from_mapping(payload)
    finally:
        await layer.group_discard(group, channel)
