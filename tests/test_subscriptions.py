"""Tests for ``changes`` model-change subscriptions and REBAC gating."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator, Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest
import strawberry
import strawberry_django
from asgiref.sync import sync_to_async
from channels.layers import InMemoryChannelLayer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models.signals import post_save
from django.test import override_settings
from rebac import (
    RelationshipTuple,
    actor_context,
    anonymous_actor,
    current_actor,
    delete_relationship,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.graphql.strawberry import RebacChannelsConsumerMixin
from rebac.roles import grant as grant_role
from rebac.roles import revoke as revoke_role
from strawberry_django.utils.requests import get_request

from angee.graphql import publishing, subscriptions
from angee.graphql.events import ChangeEvent, ChangePayload, ChangeRelatedRecord
from angee.graphql.mixins import ChangeBroadcastMixin
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.subscriptions import changes
from angee.iam import schema as iam_schema
from angee.iam.graphql import access
from angee.iam.graphql.actions import session_user
from angee.iam.graphql.consumers import IAMGraphQLWSConsumer
from angee.iam.identity import user_display_label, user_label
from angee.iam.models import UserAccountAction
from angee.iam.schema import IAMQuery
from angee.timeline import schema as timeline_schema
from tests.conftest import SchemaAddon, create_platform_admin
from tests.scopedemo.models import FactoryDoc, Scope, ScopedDoc

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


def test_schema_build_does_not_wire_change_publishers_from_subscription_metadata() -> None:
    """Building a schema is not the publisher-connection side effect."""

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
        assert _receiver_count(post_save, dispatch_uid) == 0
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
        ready = asyncio.Event()
        stream = subscriptions.subscribe(Group, ready=ready)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.wait_for(ready.wait(), timeout=1)
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


def test_subscribe_requires_a_channel_layer(monkeypatch) -> None:
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: None)

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="require a channel layer"):
            await subscriptions.subscribe(Group).__anext__()

    asyncio.run(scenario())


def test_subscription_renews_its_group_lease_and_releases_it_on_close(monkeypatch) -> None:
    """A live subscriber keeps refreshing membership; closing cancels renewal and leaves."""

    layer = InMemoryChannelLayer()
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: layer)
    monkeypatch.setattr(subscriptions, "CHANGE_GROUP_RENEW_SECONDS", 0.02)
    group = publishing.change_group(Group)

    async def scenario() -> tuple[float, float, dict[str, float], int]:
        stream = subscriptions.subscribe(Group)
        pending = asyncio.ensure_future(stream.__anext__())
        await asyncio.sleep(0.01)
        (joined,) = layer.groups[group].values()
        await asyncio.sleep(0.1)
        (renewed,) = layer.groups[group].values()
        pending.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pending
        await stream.aclose()
        renewals = [task for task in asyncio.all_tasks() if task.get_name().startswith("angee-change-lease:")]
        return joined, renewed, dict(layer.groups.get(group, {})), len(renewals)

    joined, renewed, remaining, renewals = asyncio.run(scenario())
    assert renewed > joined
    assert remaining == {}
    assert renewals == 0


def test_resolver_ready_follows_registration_and_precedes_buffered_data(monkeypatch) -> None:
    """A held group join emits nothing; data arriving at join follows the empty control."""

    entered = asyncio.Event()
    release = asyncio.Event()

    class Layer(InMemoryChannelLayer):
        async def group_add(self, group: str, channel: str) -> None:
            entered.set()
            await release.wait()
            await super().group_add(group, channel)
            await self.send(channel, {"type": "angee.change", "payload": _payload(id="7")})

    layer = Layer()
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: layer)
    monkeypatch.setattr(subscriptions, "_change_filters", lambda *args: object())
    seen = []

    def filter_event(gate, payload):
        seen.append(payload.id)
        return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "_filter_change_event", filter_event)
    resolver = _subscription_resolver(changes(Group, field="groupChanged"))

    async def scenario() -> None:
        with actor_context(ANON):
            stream = resolver(object(), SimpleNamespace(context={}))
            pending = asyncio.ensure_future(anext(stream))
            try:
                await asyncio.wait_for(entered.wait(), timeout=1)
                assert not pending.done()
                release.set()
                event = await asyncio.wait_for(pending, timeout=1)
                assert (event.model, event.action, event.id) == ("auth.Group", "ready", "")
                assert event.changed_fields is None
                assert event.changed_values is None
                assert event.occurrence_id is None
                assert event.related_records == []
                assert seen == []
                data = await asyncio.wait_for(anext(stream), timeout=1)
                assert (data.action, data.id) == ("update", "7")
                assert seen == ["7"]
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await stream.aclose()
            assert not layer.groups.get(publishing.change_group(Group))
            assert not any(task.get_name().startswith("angee-change-lease:") for task in asyncio.all_tasks())

    asyncio.run(scenario())


def test_resolver_readiness_never_exposes_a_denied_payload(monkeypatch) -> None:
    """The control contains no row data and does not exempt data from the read gate."""

    async def hidden_payload(model, *, ready):
        ready.set()
        yield _payload(id="private-row", changed_values={"name": "private-value"})

    monkeypatch.setattr(subscriptions, "subscribe", hidden_payload)
    monkeypatch.setattr(subscriptions, "_change_filters", lambda *args: object())
    monkeypatch.setattr(subscriptions, "_filter_change_event", lambda *args: None)
    resolver = _subscription_resolver(changes(Group, field="groupChanged"))

    async def scenario() -> list[ChangeEvent]:
        with actor_context(ANON):
            return [event async for event in resolver(object(), SimpleNamespace(context={}))]

    (event,) = asyncio.run(scenario())
    assert (event.action, event.id, event.changed_values) == ("ready", "", None)


def test_resolver_registration_failure_does_not_emit_ready(monkeypatch) -> None:
    """A missing transport fails promptly rather than claiming readiness or hanging."""

    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: None)
    monkeypatch.setattr(subscriptions, "_change_filters", lambda *args: object())
    resolver = _subscription_resolver(changes(Group, field="groupChanged"))

    async def scenario() -> None:
        with actor_context(ANON):
            stream = resolver(object(), SimpleNamespace(context={}))
            try:
                with pytest.raises(RuntimeError, match="require a channel layer"):
                    await asyncio.wait_for(anext(stream), timeout=1)
            finally:
                await stream.aclose()

    asyncio.run(scenario())


def test_resolver_cancellation_after_ready_releases_membership(monkeypatch) -> None:
    """Disconnect while waiting for data cancels the receive and native lease."""

    layer = InMemoryChannelLayer()
    monkeypatch.setattr(subscriptions, "change_channel_layer", lambda: layer)
    monkeypatch.setattr(subscriptions, "_change_filters", lambda *args: object())
    resolver = _subscription_resolver(changes(Group, field="groupChanged"))

    async def scenario() -> None:
        with actor_context(ANON):
            stream = resolver(object(), SimpleNamespace(context={}))
            assert (await asyncio.wait_for(anext(stream), timeout=1)).action == "ready"
            pending = asyncio.ensure_future(anext(stream))
            await asyncio.sleep(0)
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
            await stream.aclose()
        assert not layer.groups.get(publishing.change_group(Group))
        assert not any(task.get_name().startswith("angee-change-lease:") for task in asyncio.all_tasks())

    asyncio.run(scenario())


def test_subscription_resolver_gates_events_through_sync_adapter(
    monkeypatch,
) -> None:
    """Gate construction and filtering do not use the shared thread-sensitive executor."""

    closed_connections: list[str] = []
    thread_flags: list[bool] = []

    async def subscribe(model: type[Group], *, ready: asyncio.Event):
        """Yield one payload after registering without touching the channel layer."""

        assert model is Group
        ready.set()
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

    monkeypatch.setattr(subscriptions, "subscribe", subscribe)
    monkeypatch.setattr("angee.iam.graphql.changes.ChangeReadGate", Gate)
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
    assert [(event.action, event.id) for event in events] == [("ready", ""), ("update", "9")]


def test_subscription_resolver_uses_current_actor(monkeypatch) -> None:
    """Subscription gating reads the ambient REBAC actor."""

    seen: list[object] = []

    async def subscribe(model: type[Group], *, ready: asyncio.Event):
        """Yield one payload after registering without touching the channel layer."""

        assert model is Group
        ready.set()
        yield _payload(id="10")

    class Gate:
        """Gate stub that records the actor it gates for."""

        def __init__(self, model: type[Group], actor: object) -> None:
            assert model is Group
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            seen.append((self.actor, current_actor()))
            return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "subscribe", subscribe)
    monkeypatch.setattr("angee.iam.graphql.changes.ChangeReadGate", Gate)
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
    assert [(event.action, event.id) for event in events] == [("ready", ""), ("update", "10")]


def test_subscription_resolver_denies_without_current_actor(
    monkeypatch,
) -> None:
    """A subscription with no ambient actor yields no change events."""

    calls: list[object] = []

    async def subscribe(model: type[Group], *, ready: asyncio.Event):
        """Yield one payload after registering without touching the channel layer."""

        assert model is Group
        ready.set()
        yield _payload(id="11")

    class Gate:
        """Gate stub that fails the test if no-actor subscriptions reach it."""

        def __init__(self, model: type[Group], actor: object) -> None:
            self.actor = actor

        def filter(self, payload: dict[str, object]) -> ChangeEvent:
            calls.append(payload)
            return ChangeEvent.from_payload(payload)

    monkeypatch.setattr(subscriptions, "subscribe", subscribe)
    monkeypatch.setattr("angee.iam.graphql.changes.ChangeReadGate", Gate)
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


def test_change_occurrence_identity_round_trips_and_legacy_payloads_remain_unidentified() -> None:
    payload = ChangePayload(
        model="tests.Row",
        id="1",
        action="update",
        occurrence_id="change-1",
        related_records=(ChangeRelatedRecord(model="tests.Parent", id="parent-1"),),
    )
    assert ChangePayload.from_mapping(payload.as_message()).occurrence_id == "change-1"
    assert ChangePayload.from_mapping(payload.as_message()).related_records == payload.related_records
    assert ChangePayload.from_mapping({"model": "tests.Row", "id": "1", "action": "update"}).occurrence_id is None
    assert payload.redacted({"secret"}).occurrence_id == "change-1"


def test_change_gate_omits_an_unreadable_related_parent(monkeypatch) -> None:
    """A readable child event never discloses a parent outside the actor's read scope."""

    gate = object.__new__(access.ChangeReadGate)
    gate.actor = ANON
    monkeypatch.setattr(access.apps, "get_model", lambda label: ScopedDoc)
    scopes = []

    def unreadable(model, public_ids, *, queryset):
        assert model is ScopedDoc
        assert queryset.model is ScopedDoc
        scopes.append((queryset, tuple(public_ids)))
        return {}

    monkeypatch.setattr(access, "instances_from_public_ids", unreadable)
    references = tuple(ChangeRelatedRecord(model="tests.Parent", id=f"parent-{index}") for index in range(5))
    change = ChangePayload(
        model="tests.Child",
        id="child-1",
        action="update",
        related_records=references,
    )
    assert gate._filter_related_records(change).related_records == ()
    assert len(scopes) == 1
    assert scopes[0][1] == tuple(reference.id for reference in references)


def test_publish_respects_broadcasts_changes_optout(monkeypatch) -> None:
    """``publish_change`` keeps opted-out rows off the subscription transport.

    The emission mirror of a read scope that hides rows: a model isolates some rows
    (record chatter) to a record-scoped surface, so their changes never reach the
    generic ``changes`` subscription — checked while the instance is live, so it
    holds for deletes too. A row without the hook broadcasts by default.
    """

    sent: list[dict[str, object]] = []
    monkeypatch.setattr(publishing, "_broadcast", lambda model, payload: sent.append(payload))
    # Run the on_commit callback inline so the (non-)broadcast is observable now.
    monkeypatch.setattr(publishing.transaction, "on_commit", lambda callback, **kwargs: callback())

    class Row(ChangeBroadcastMixin, Group):
        """Native proxy row carrying the broadcast opt-out decision."""

        class Meta:
            proxy = True
            app_label = "auth"

        def __init__(self, broadcasts: bool) -> None:
            super().__init__(pk=1, name="broadcast-policy")
            self._broadcasts = broadcasts

        def broadcasts_changes(self) -> bool:
            return self._broadcasts

    publishing.publish_change(Row(broadcasts=False), action="update", update_fields=None)
    assert sent == []

    publishing.publish_change(Row(broadcasts=True), action="delete", update_fields=None)
    assert len(sent) == 1
    emitted = ChangePayload.from_mapping(sent[0])
    assert (emitted.model, emitted.id, emitted.action, emitted.broadcasts) == ("auth.Row", "1", "delete", True)

    # A plain model with no hook keeps the default broadcast behavior.
    publishing.publish_change(Group(pk=2, name="default-broadcast"), action="create", update_fields=None)
    assert len(sent) == 2
    emitted = ChangePayload.from_mapping(sent[1])
    assert (emitted.model, emitted.id, emitted.action, emitted.broadcasts) == ("auth.Group", "2", "create", True)


@pytest.mark.parametrize(
    ("debug", "broker", "eager", "allow", "safe"),
    [
        (True, "redis://localhost:6379/0", False, False, False),
        (False, "redis://localhost:6379/0", False, False, False),
        (True, "redis://localhost:6379/0", True, False, True),
        (False, "redis://localhost:6379/0", True, False, False),
        (True, "redis://localhost:6379/0", False, True, True),
        (True, None, False, False, True),
        (True, "amqp://guest:guest@localhost//", False, False, False),
    ],
)
def test_inmemory_channel_layer_requires_eager_dev_or_test_opt_in(
    debug: bool,
    broker: str | None,
    eager: bool,
    allow: bool,
    safe: bool,
) -> None:
    """A non-eager worker cannot share an in-memory subscription transport."""

    with override_settings(
        DEBUG=debug,
        CELERY_TASK_ALWAYS_EAGER=eager,
        CELERY_BROKER_URL=broker,
        ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER=allow,
        CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
    ):
        if safe:
            assert publishing.change_channel_layer() is not None
        else:
            with pytest.raises(ImproperlyConfigured, match="cannot deliver GraphQL changes across processes"):
                publishing.change_channel_layer()


def test_ws_consumer_uses_rebac_channels_mixin() -> None:
    """The GraphQL WS consumer delegates actor setup to REBAC."""

    assert issubclass(IAMGraphQLWSConsumer, RebacChannelsConsumerMixin)


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
        application = IAMGraphQLWSConsumer.as_asgi(schema=strawberry.Schema(query=Query))
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


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TIMELINE_KINDS=[])
def test_ws_queries_refresh_identity_eligibility_and_timeline_admission(composed_tables: None) -> None:
    """Separate operations on one native consumer never reuse HTTP request memos."""

    users = get_user_model()
    user = users.objects.create_user(
        username="websocket-identity", first_name="WebSocket", last_name="Reader",
    )
    target = create_platform_admin("websocket-target", first_name="First", last_name="Label")
    with system_context(reason="test.websocket.timeline_subject"):
        scope = Scope.objects.create(name="WebSocket admission")
        record = FactoryDoc.objects.create(scope=scope, title="Scoped timeline")
        membership = RelationshipTuple(to_object_ref(scope), "direct_member", to_subject_ref(user))
        write_relationships([membership])

    @strawberry.type
    class Query(IAMQuery):
        @strawberry.field
        def session_label(self, info: strawberry.Info) -> str:
            return user_label(session_user(info))

        @strawberry_django.field
        def target_label(self, info: strawberry.Info) -> str | None:
            return user_display_label(target.pk, request=get_request(info))

        @strawberry_django.field
        def target_can_rename(self, info: strawberry.Info) -> bool:
            allowed = iam_schema._account_action_allowed(UserAccountAction.RENAME, info)
            return bool(users._base_manager.filter(pk=target.pk).annotate(allowed=allowed).values_list(
                "allowed", flat=True,
            ).get())

        @strawberry_django.field
        def record_admitted(self, info: strawberry.Info) -> bool:
            try:
                timeline_schema._timeline(info, record._meta.label, record.public_id)
            except ValidationError as error:
                assert error.code == "not_found"
                return False
            return True

    schema = GraphQLSchemas([SchemaAddon({"public": {"query": (Query,)}})]).build("public")

    def change_labels_and_policy() -> None:
        with system_context(reason="test.websocket.policy_changed"):
            users._base_manager.filter(pk=target.pk).update(first_name="Second")
            revoke_role(actor=target, role="angee/role:admin")
            grant_role(actor=user, role="angee/role:admin")
            delete_relationship(membership)

    async def scenario() -> list[dict[str, object]]:
        communicator = WebsocketCommunicator(
            IAMGraphQLWSConsumer.as_asgi(schema=schema),
            "/graphql/public/",
            subprotocols=["graphql-transport-ws"],
        )
        communicator.scope["user"] = user
        connected, _subprotocol = await communicator.connect()
        assert connected
        try:
            await communicator.send_json_to({"type": "connection_init"})
            assert await communicator.receive_json_from() == {"type": "connection_ack"}
            results = []
            for operation in ("before", "after"):
                if operation == "after":
                    await sync_to_async(change_labels_and_policy)()
                await communicator.send_json_to({
                    "id": operation, "type": "subscribe",
                    "payload": {"query": """{
                        current_user { display_name } session_label
                        target_label target_can_rename record_admitted
                    }"""},
                })
                results.append(await communicator.receive_json_from())
                assert await communicator.receive_json_from() == {"id": operation, "type": "complete"}
            return results
        finally:
            await communicator.disconnect()

    assert asyncio.run(scenario()) == [
        {"id": operation, "type": "next", "payload": {"data": {
            "current_user": {"display_name": "WebSocket Reader"}, "session_label": "WebSocket Reader",
            "target_label": label, "target_can_rename": eligible, "record_admitted": admitted,
        }}}
        for operation, label, eligible, admitted in (
            ("before", "First Label", False, True), ("after", "Second Label", True, False),
        )
    ]
    assert current_actor() is None
