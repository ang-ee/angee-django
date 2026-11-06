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
from angee.graphql.replay import Replay, read_changes

logger = logging.getLogger(__name__)


REPLAY_BATCH_MAX = 16


class _Registration:
    """Coordinate the GraphQL replay after group join and gate construction."""

    def __init__(self) -> None:
        self.joined = asyncio.Event()
        self.capture = asyncio.Event()
        self.ready = asyncio.Event()
        self.snapshot: Replay | None = None


def changes(
    model: type[models.Model],
    *,
    field: str,
    read_gate: type | None = None,
    replay: bool = True,
) -> type:
    """Stream a registration-ready control followed by read-gated model changes.

    ``ready`` has this model's label and an empty id. With ``replay``, it also
    carries a cursor, and retained changes pass the actor's read gate before it.
    A live-only stream passes ``ready`` to the transport instead of registration.
    """

    label = model._meta.label
    gate_type = read_gate

    async def resolve(
        self: object,
        info: strawberry.Info,
    ) -> AsyncGenerator[ChangeEvent, None]:
        """Yield read-gated change events for one subscriber."""

        del self
        # Register first: the gate may wait on the ORM's sync executor.
        registration = _Registration()
        stream_source = (
            subscribe(model, registration=registration) if replay
            else subscribe(model, ready=registration.joined)
        )
        async with contextlib.aclosing(stream_source) as stream:
            # An async generator registers only when advanced. Start its first
            # receive before waiting, and surface registration failures promptly.
            pending = asyncio.ensure_future(anext(stream))
            joined = asyncio.create_task(registration.joined.wait())
            registered = asyncio.create_task(registration.ready.wait())
            try:
                await asyncio.wait((joined, pending), return_when=asyncio.FIRST_COMPLETED)
                if not registration.joined.is_set():
                    await pending
                    return
                # Gate construction remains on the general sync executor and
                # brackets ORM work with Django's connection lifecycle.
                filters = await sync_to_async(_change_filters, thread_sensitive=False)(model, info, gate_type)
                if filters is None:
                    return
                if replay:
                    registration.capture.set()
                else:
                    registration.ready.set()
                await asyncio.wait((registered, pending), return_when=asyncio.FIRST_COMPLETED)
                if not registration.ready.is_set():
                    await pending
                    return

                async def gated(payload: ChangePayload) -> ChangeEvent | None:
                    return await sync_to_async(_filter_change_event, thread_sensitive=False)(filters, payload)

                cursor = None
                if replay:
                    snapshot = registration.snapshot
                    assert snapshot is not None
                    if snapshot.truncated or snapshot.incomplete or len(snapshot.changes) > REPLAY_BATCH_MAX:
                        yield ChangeEvent(model=label, id=strawberry.ID(""), action="update")
                    else:
                        retained = []
                        for _ in snapshot.changes:
                            retained.append(await pending)
                            pending = asyncio.ensure_future(anext(stream))
                        if retained:
                            events = await sync_to_async(_filter_replay_events, thread_sensitive=False)(
                                filters, tuple(retained),
                            )
                            for replayed_event in events:
                                yield replayed_event
                            if events:
                                del replayed_event
                            del events
                        del retained
                    cursor = snapshot.cursor
                    registration.snapshot = None
                    del snapshot
                yield ChangeEvent(model=label, id=strawberry.ID(""), action="ready", replay_cursor=cursor)
                while True:
                    try:
                        payload = await pending
                    except StopAsyncIteration:
                        return
                    event = await gated(payload)
                    if event is not None:
                        yield event
                    pending = asyncio.ensure_future(anext(stream))
            finally:
                # Cancel the outstanding receive before closing the generator;
                # its native finally releases group membership and renewal.
                pending.cancel()
                joined.cancel()
                registered.cancel()
                await asyncio.gather(pending, joined, registered, return_exceptions=True)

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


def _filter_replay_events(
    filters: tuple[Callable[..., Any], ...], payloads: tuple[ChangePayload, ...],
) -> tuple[ChangeEvent, ...]:
    """Evaluate a bounded registration batch on one sync executor hop."""

    close_old_connections()
    try:
        if filters:
            owner = getattr(filters[0], "__self__", None)
            batch = getattr(owner, "filter_many", None)
            if batch is not None:
                return batch(payloads)
        return tuple(event for payload in payloads if (event := (
            filters[0](payload) if filters else ChangeEvent.from_payload(payload)
        )) is not None)
    finally:
        close_old_connections()


async def subscribe(
    model: type[models.Model],
    *,
    ready: asyncio.Event | None = None,
    registration: _Registration | None = None,
) -> AsyncGenerator[ChangePayload, None]:
    """Yield live changes, optionally replaying for a GraphQL registration.

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
        if registration is not None:
            registration.joined.set()
            await registration.capture.wait()
            snapshot = await read_changes(layer, group)
            registration.snapshot = snapshot
            retained = (
                () if snapshot.truncated or snapshot.incomplete or len(snapshot.changes) > REPLAY_BATCH_MAX
                else snapshot.changes
            )
            replayed = {payload.occurrence_id for payload in retained if payload.occurrence_id is not None}
            registration.ready.set()
        else:
            retained = ()
            replayed = set()
        if ready is not None:
            ready.set()
        for payload in retained:
            yield payload
        if retained:
            del payload
        del retained
        if registration is not None:
            del snapshot
        while True:
            message = await layer.receive(channel)
            payload = message.get("payload")
            if payload:
                change = ChangePayload.from_mapping(payload)
                if change.occurrence_id is not None and change.occurrence_id in replayed:
                    replayed.discard(change.occurrence_id)
                    continue
                yield change
    finally:
        # Stop renewing before leaving, so an in-flight renewal cannot re-add.
        renewal.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await renewal
        await layer.group_discard(group, channel)
