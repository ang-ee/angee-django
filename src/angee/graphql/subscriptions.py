"""GraphQL subscription surfaces for model change events."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from typing import Any

import strawberry
from asgiref.sync import sync_to_async
from django.db import close_old_connections, models
from angee.base.hooks import resolve_hook

from angee.graphql.constants import CHANGE_GROUP_RENEW_SECONDS
from angee.graphql.data.contracts import DataResourceRoots, DataResourceTypeNames
from angee.graphql.data.metadata import (
    DataResourceContribution,
    attach_data_resource_contribution,
    resource_wire_field_name,
)
from angee.graphql.events import ChangeEvent, ChangePayload
from angee.graphql.publishing import change_channel_layer, change_group

logger = logging.getLogger(__name__)


def changes(
    model: type[models.Model],
    *,
    field: str,
    read_gate: type | None = None,
) -> type:
    """Stream a registration-ready control followed by read-gated model changes.

    ``ready`` has this model's label and an empty id, with no record data. It
    confirms group registration for snapshot reconciliation, not event replay.
    """

    label = model._meta.label
    gate_type = read_gate

    async def resolve(
        self: object,
        info: strawberry.Info,
    ) -> AsyncGenerator[ChangeEvent, None]:
        """Yield read-gated change events for one subscriber."""

        del self
        # The gate work stays on the general sync executor so subscriptions can
        # filter concurrently. The wrappers bracket that thread's ORM work with
        # Django's connection lifecycle because long-lived subscriptions do not
        # ride the normal request boundary.
        filters = await sync_to_async(_change_filters, thread_sensitive=False)(model, info, gate_type)
        if filters is None:
            return
        ready = asyncio.Event()
        async with contextlib.aclosing(subscribe(model, ready=ready)) as stream:
            # An async generator registers only when advanced. Start its first
            # receive before waiting, and surface registration failures promptly.
            pending = asyncio.ensure_future(anext(stream))
            registered = asyncio.create_task(ready.wait())
            try:
                await asyncio.wait((registered, pending), return_when=asyncio.FIRST_COMPLETED)
                if not ready.is_set():
                    await pending
                    return
                yield ChangeEvent(model=label, id=strawberry.ID(""), action="ready")
                while True:
                    try:
                        payload = await pending
                    except StopAsyncIteration:
                        return
                    event = await sync_to_async(_filter_change_event, thread_sensitive=False)(filters, payload)
                    if event is not None:
                        yield event
                    pending = asyncio.ensure_future(anext(stream))
            finally:
                # Cancel the outstanding receive before closing the generator;
                # its native finally releases group membership and renewal.
                pending.cancel()
                registered.cancel()
                await asyncio.gather(pending, registered, return_exceptions=True)

    resolve.__name__ = field
    namespace = {field: strawberry.subscription(resolver=resolve, name=field)}
    surface = type(f"{model.__name__}Subscription", (), namespace)
    surface.__doc__ = f"Live changes to {label}."
    surface = strawberry.type(surface)
    return attach_data_resource_contribution(
        surface,
        DataResourceContribution(
            model=model,
            model_label=model._meta.label,
            roots=DataResourceRoots(changes_name=resource_wire_field_name(surface, field)),
            type_names=DataResourceTypeNames(),
            capabilities=("changes",),
        ),
    )


def _change_filters(model: type[models.Model], info: strawberry.Info, gate_type: type | None):
    """Construct declared subscription filters with healthy Django connections."""
    close_old_connections()
    try:
        provider = resolve_hook("ANGEE_GRAPHQL_CHANGE_FILTER")
        if provider is None:
            if gate_type is not None:
                raise RuntimeError("A custom change read gate requires a declared change-filter provider.")
            return ()
        selected = provider(model, info, gate_type)
        return None if selected is None else (selected,)
    finally:
        close_old_connections()


def _filter_change_event(filters: tuple[Callable[..., Any], ...], payload: ChangePayload) -> ChangeEvent | None:
    """Apply concrete contributors around the one native event projection."""
    close_old_connections()
    try:
        return filters[0](payload) if filters else ChangeEvent.from_payload(payload)
    finally:
        close_old_connections()


async def subscribe(
    model: type[models.Model],
    *,
    ready: asyncio.Event | None = None,
) -> AsyncGenerator[ChangePayload, None]:
    """Yield model changes; signal ``ready`` after subscribing, before a caller's snapshot.

    Payloads are unfiltered. Callers must read through their actor's row scope.
    A change publisher must be connected for this model, normally through a
    ``changes(...)`` declaration. Without a channel layer this raises immediately.
    """

    layer = change_channel_layer()
    if layer is None:
        raise RuntimeError("Model changes require a channel layer.")
    group = change_group(model)
    channel = await layer.new_channel()
    await layer.group_add(group, channel)

    async def renew_lease() -> None:
        # Membership is a lease: a process that dies without discarding (a
        # restart) lets it expire instead of leaving a dead member for a day.
        while True:
            await asyncio.sleep(CHANGE_GROUP_RENEW_SECONDS)
            try:
                await layer.group_add(group, channel)
            except Exception:
                logger.warning("Renewing the %s change subscription failed.", group, exc_info=True)

    renewal = asyncio.create_task(renew_lease(), name=f"angee-change-lease:{group}")
    try:
        if ready is not None:
            ready.set()
        while True:
            message = await layer.receive(channel)
            payload = message.get("payload")
            if payload:
                yield ChangePayload.from_mapping(payload)
    finally:
        # Stop renewing before leaving, so an in-flight renewal cannot re-add.
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal
        await layer.group_discard(group, channel)
