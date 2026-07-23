"""Tests for the Discord bot Gateway addon without importing discord.py."""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.credentials import CredentialKind
from angee.integrate.live import PairingState, session_store_path
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.sync import BridgeProgressReporter
from tests.conftest import (
    SchemaAddon,
    Vendor,
    _clear_model_tables,
    _create_missing_tables,
    execute_schema,
    result_data,
)
from tests.test_messaging import MESSAGING_TEST_MODELS, Message
from tests.test_messaging_graphql import (
    Channel,
    _platform_admin,
    _request,
    iam_schema,
    integrate_schema,
    messaging_schema,
    parties_schema,
)

DISCORD_TEST_MODELS = (*MESSAGING_TEST_MODELS, Channel)
Credential = apps.get_model("integrate", "Credential")


def _discord_module(name: str) -> ModuleType:
    """Import one console-safe Discord addon module."""

    return importlib.import_module(f"angee.messaging_integrate_discord.{name}")


@pytest.fixture
def discord_tables(
    tmp_path: Path,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> Any:
    """Create concrete messaging tables and isolate Discord session storage."""

    settings.ANGEE_DATA_DIR = str(tmp_path / "data")
    monkeypatch.setattr("angee.integrate.impl.enqueue_task", lambda *args, **kwargs: None)
    created_models = _create_missing_tables(DISCORD_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(DISCORD_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _gateway_message(**values: Any) -> dict[str, Any]:
    """Return an SDK-free Discord Gateway message fixture."""

    message = {
        "id": "175928847299117063",
        "type": 19,
        "content": "Hello from Discord",
        "timestamp_ms": 1_768_469_400_000,
        "channel": {
            "id": "175928847299117060",
            "name": "general",
            "guild": {
                "id": "175928847299117050",
                "name": "Angee",
            },
        },
        "author": {
            "id": "175928847299117001",
            "username": "ada",
            "global_name": "Ada Lovelace",
        },
        "attachments": [
            {
                "url": "https://cdn.discordapp.com/attachments/a/b/photo.jpg?ex=1&is=2&hm=3",
                "filename": "photo.jpg",
                "content_type": "image/jpeg",
                "size": 4,
            }
        ],
        "message_reference": {
            "channel_id": "175928847299117060",
            "message_id": "175928847299117061",
        },
    }
    message.update(values)
    return message


def test_discord_identity_maps_guild_reply_author_and_signed_attachment() -> None:
    """Gateway dictionaries map directly onto the neutral ingest boundary."""

    identity = _discord_module("identity")

    parsed = identity.parsed_message(_gateway_message(), own_id="999")

    assert parsed is not None
    assert parsed.external_id == "175928847299117060/175928847299117063"
    assert parsed.platform == "discord"
    assert parsed.direction == "inbound"
    assert parsed.sender is not None
    assert parsed.sender.external_id == "175928847299117001"
    assert parsed.sender.value == "ada"
    assert parsed.sender.display_name == "Ada Lovelace"
    assert parsed.sent_at == datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc)
    assert parsed.in_reply_to == "175928847299117060/175928847299117061"
    assert parsed.thread is not None
    assert parsed.thread.external_id == "175928847299117060"
    assert parsed.thread.modality == "group"
    assert parsed.thread.title == "general"
    assert parsed.body is not None and parsed.body.text == "Hello from Discord"
    assert parsed.metadata["_media_facts"] == (
        identity.DiscordMediaFact(
            url="https://cdn.discordapp.com/attachments/a/b/photo.jpg?ex=1&is=2&hm=3",
            mime="image/jpeg",
            name="photo.jpg",
            size=4,
        ),
    )


def test_discord_identity_maps_bot_dm_as_outbound_direct() -> None:
    """A DM sent by the bot uses the direct modality and outbound direction."""

    identity = _discord_module("identity")
    wire = _gateway_message(
        channel={
            "id": "275928847299117060",
            "name": "Ada",
            "guild": None,
        },
        author={
            "id": "999",
            "username": "angee-bot",
            "global_name": "",
        },
        attachments=[],
        message_reference=None,
    )

    parsed = identity.parsed_message(wire, own_id="999")

    assert parsed is not None and parsed.direction == "outbound"
    assert parsed.thread is not None and parsed.thread.modality == "direct"
    assert parsed.thread.title == "Ada"


@pytest.mark.parametrize(
    "wire",
    [
        _gateway_message(type=7),
        _gateway_message(type="pins_add"),
        _gateway_message(content="", attachments=[], embeds=[{"title": "deferred"}]),
        _gateway_message(content="", attachments=[], sticker_items=[{"id": "1"}]),
    ],
)
def test_discord_v1_skips_system_and_deferred_message_shapes(wire: dict[str, Any]) -> None:
    """System, embed-only, and sticker-only events do not enter ingest."""

    assert _discord_module("identity").parsed_message(wire) is None


def _discord_channel(user: Any, *, token: str = "valid-bot-token", name: str = "Community bot") -> Any:
    """Create one connected Discord channel through its public service."""

    with system_context(reason="test.messaging.discord.vendor.seed"):
        Vendor.objects.get_or_create(slug="discord", defaults={"display_name": "Discord"})
    return _discord_module("connect").create_discord_channel(user, name, token)


@pytest.mark.django_db(transaction=True)
def test_create_discord_channel_owns_atomic_static_token_and_live_start(discord_tables: Any) -> None:
    """Connect stores only the bot token credential and starts the live channel."""

    admin = _platform_admin("msg-discord-connect-admin")
    channel = _discord_channel(admin)

    with system_context(reason="test.messaging.discord.connect.verify"):
        channel.refresh_from_db()
        assert channel.vendor.slug == "discord"
        assert channel.backend_class == "discord"
        assert channel.display_name == "Community bot"
        assert channel.lifecycle == "connected"
        assert channel.subscription_state["desired"] == Channel.LiveState.LIVE
        assert channel.credential.kind == CredentialKind.STATIC_TOKEN
        assert channel.credential.reveal() == {"api_key": "valid-bot-token"}


@pytest.mark.django_db(transaction=True)
def test_connect_discord_channel_mutation_dispatches_to_service(discord_tables: Any) -> None:
    """The admin-only mutation returns the shared Channel projection."""

    admin = _platform_admin("msg-discord-graphql-admin")
    with system_context(reason="test.messaging.discord.graphql.seed"):
        Vendor.objects.create(slug="discord", display_name="Discord")
    discord_schema = _discord_module("schema")
    addons = [
        SchemaAddon({"console": {key: tuple(module.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}})
        for module in (iam_schema, integrate_schema, parties_schema, messaging_schema, discord_schema)
    ]
    schema = GraphQLSchemas(addons).build("console")

    result = execute_schema(
        schema,
        """
        mutation ConnectDiscord($name: String!, $token: String!) {
          connect_discord_channel(name: $name, token: $token) {
            id
            display_name
            backend_class
            lifecycle
          }
        }
        """,
        {"name": "Community bot", "token": "valid-bot-token"},
        request=_request(admin),
    )

    assert result_data(result)["connect_discord_channel"] == {
        "id": result_data(result)["connect_discord_channel"]["id"],
        "display_name": "Community bot",
        "backend_class": "DISCORD",
        "lifecycle": "CONNECTED",
    }


class _FakeLoginFailure(Exception):
    """Fake of discord.LoginFailure."""


class _FakeHTTPException(Exception):
    """Fake Discord HTTP failure carrying its status."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(str(status))


class _FakeForbidden(_FakeHTTPException):
    """Fake unreadable-channel response."""


class _FakeNotFound(_FakeHTTPException):
    """Fake missing-channel response."""


class _FakeIntents:
    """Capture the exact Gateway intent switches."""

    def __init__(self, **values: bool) -> None:
        self.values = values


class _FakeObject:
    """Discord snowflake boundary accepted by channel.history(after=...)."""

    def __init__(self, *, id: int) -> None:
        self.id = id


class _FakeDiscordChannel:
    """Async REST history source used by the session tests."""

    def __init__(
        self,
        channel_id: int,
        name: str = "general",
        *,
        last_message_id: int | None = None,
    ) -> None:
        self.id = channel_id
        self.name = name
        self.last_message_id = last_message_id
        self.guild = SimpleNamespace(
            id=900,
            name="Angee",
            me=SimpleNamespace(id=999),
            threads=[],
        )
        self.messages: list[Any] = []
        self.history_calls: list[dict[str, Any]] = []
        self.respect_after = True

    def history(self, *, after: Any, limit: int, oldest_first: bool) -> Any:
        """Yield a bounded history page and record its snowflake watermark."""

        self.history_calls.append(
            {
                "after": getattr(after, "id", None),
                "limit": limit,
                "oldest_first": oldest_first,
            }
        )

        async def iterate() -> Any:
            yielded = 0
            for message in sorted(
                self.messages,
                key=lambda item: item.id,
                reverse=not oldest_first,
            ):
                if self.respect_after and after is not None and message.id <= after.id:
                    continue
                if yielded >= limit:
                    break
                yielded += 1
                yield message

        return iterate()

    def permissions_for(self, _member: Any) -> Any:
        return SimpleNamespace(view_channel=True, read_message_history=True)


def _sdk_message(
    channel: _FakeDiscordChannel,
    message_id: int,
    *,
    content: str = "Hello from Discord",
    attachment: Any = None,
    reply_to: int | None = None,
) -> Any:
    """Return one discord.py-shaped message object."""

    return SimpleNamespace(
        id=message_id,
        type=SimpleNamespace(value=0),
        content=content,
        created_at=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
        channel=channel,
        guild=channel.guild,
        author=SimpleNamespace(
            id=123,
            name="ada",
            username="ada",
            global_name="Ada Lovelace",
        ),
        attachments=[] if attachment is None else [attachment],
        reference=(None if reply_to is None else SimpleNamespace(message_id=reply_to, channel_id=channel.id)),
    )


class _FakeDiscordClient:
    """discord.py Client boundary with event dispatch and blocking start."""

    instances: list[_FakeDiscordClient] = []
    channels: list[_FakeDiscordChannel] = []
    live_messages: list[Any] = []

    def __init__(self, *, intents: _FakeIntents) -> None:
        self.intents = intents
        self.user = SimpleNamespace(id=999, name="angee-bot")
        self.guilds = [channel.guild for channel in self.channels]
        self.private_channels: list[Any] = []
        self.handlers: dict[str, Any] = {}
        self.closed = False
        self.close_event: asyncio.Event | None = None
        type(self).instances.append(self)

    def event(self, handler: Any) -> Any:
        self.handlers[handler.__name__] = handler
        setattr(self, handler.__name__, handler)
        return handler

    def get_all_channels(self) -> list[_FakeDiscordChannel]:
        return list(self.channels)

    async def start(self, token: str) -> None:
        if token == "invalid-bot-token":
            raise _FakeLoginFailure("Improper token has been passed.")
        self.close_event = asyncio.Event()
        await self.handlers["on_ready"]()
        for message in self.live_messages:
            await self.handlers["on_message"](message)
        await self.close_event.wait()

    async def close(self) -> None:
        self.closed = True
        if self.close_event is not None:
            self.close_event.set()

    def is_closed(self) -> bool:
        return self.closed


@pytest.fixture
def discord_session_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load the worker session against a fake discord module."""

    discord = ModuleType("discord")
    discord.Client = _FakeDiscordClient  # type: ignore[attr-defined]
    discord.Intents = _FakeIntents  # type: ignore[attr-defined]
    discord.Object = _FakeObject  # type: ignore[attr-defined]
    discord.LoginFailure = _FakeLoginFailure  # type: ignore[attr-defined]
    discord.HTTPException = _FakeHTTPException  # type: ignore[attr-defined]
    discord.Forbidden = _FakeForbidden  # type: ignore[attr-defined]
    discord.NotFound = _FakeNotFound  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "discord", discord)
    module_name = "angee.messaging_integrate_discord.session"
    sys.modules.pop(module_name, None)
    _FakeDiscordClient.instances.clear()
    _FakeDiscordClient.channels = []
    _FakeDiscordClient.live_messages = []
    try:
        yield importlib.import_module(module_name)
    finally:
        sys.modules.pop(module_name, None)


def _wait_until(predicate: Any, *, timeout: float = 5.0) -> None:
    """Wait for one cross-thread session condition."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


@pytest.mark.django_db(transaction=True)
def test_discord_full_session_pairs_ingests_backfills_and_resumes_watermark(
    discord_tables: Any,
    discord_session_module: ModuleType,
) -> None:
    """Recent seed plus paged resume leaves no gap and live events never leap it."""

    admin = _platform_admin("msg-discord-session-admin")
    channel = _discord_channel(admin)
    sdk_channel = _FakeDiscordChannel(700, last_message_id=109)
    sdk_channel.messages = [
        _sdk_message(sdk_channel, message_id, content=f"history-{message_id}") for message_id in range(100, 110)
    ]
    _FakeDiscordClient.channels = [sdk_channel]
    _FakeDiscordClient.live_messages = [_sdk_message(sdk_channel, 110, content="live")]
    first_stop = threading.Event()
    first = discord_session_module.DiscordSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=first_stop,
    )
    failures: list[BaseException] = []

    def stop_first() -> None:
        try:
            _wait_until(
                lambda: (
                    Message._base_manager.filter(channel_id=channel.pk).count() == 6
                    and (state := Channel._base_manager.get(pk=channel.pk).subscription_state)
                    .get("channel_watermarks", {})
                    .get("700")
                    == "109"
                    and state.get("history_seeded") is True
                )
            )
        except BaseException as error:  # noqa: BLE001 — surface operator-thread failures.
            failures.append(error)
        finally:
            first_stop.set()

    operator = threading.Thread(target=stop_first, daemon=True)
    with system_context(reason="test.messaging.discord.first.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator.start()
        first_outcome = first.run()
        operator.join(timeout=5)

    assert failures == []
    assert first_outcome is PairingState.PAIRED
    assert not operator.is_alive()
    first_client = _FakeDiscordClient.instances[-1]
    assert first_client.intents.values == {
        "guilds": True,
        "guild_messages": True,
        "message_content": True,
        "dm_messages": True,
    }
    assert sdk_channel.history_calls[0] == {
        "after": None,
        "limit": 5,
        "oldest_first": False,
    }
    with system_context(reason="test.messaging.discord.first.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["own_id"] == "999"
        assert channel.subscription_state["username"] == "angee-bot"
        assert channel.subscription_state["history_seeded"] is True
        assert channel.backend.pairing().account_label == "angee-bot"
    assert {row.external_id for row in Message._base_manager.filter(channel_id=channel.pk)} == {
        f"700/{message_id}" for message_id in range(105, 111)
    }

    marker = session_store_path(channel) / "session.marker"
    assert marker.read_bytes() == b""

    sdk_channel.messages = [
        _sdk_message(sdk_channel, message_id, content=f"catch-up-{message_id}") for message_id in range(100, 223)
    ]
    sdk_channel.last_message_id = 222
    sdk_channel.history_calls.clear()
    _FakeDiscordClient.live_messages = [_sdk_message(sdk_channel, 223, content="live-2")]
    second_stop = threading.Event()
    second_channel = Channel._base_manager.get(pk=channel.pk)
    second = discord_session_module.DiscordSession(
        second_channel,
        reporter=BridgeProgressReporter(second_channel),
        stop_event=second_stop,
    )

    def stop_second() -> None:
        try:
            _wait_until(
                lambda: (
                    Message._base_manager.filter(channel_id=channel.pk).count() == 119
                    and Channel._base_manager.get(pk=channel.pk)
                    .subscription_state.get("channel_watermarks", {})
                    .get("700")
                    == "222"
                )
            )
        except BaseException as error:  # noqa: BLE001
            failures.append(error)
        finally:
            second_stop.set()

    operator = threading.Thread(target=stop_second, daemon=True)
    with system_context(reason="test.messaging.discord.second.run"), bridge_advisory_lock(channel) as acquired:
        assert acquired
        operator.start()
        second_outcome = second.run()
        operator.join(timeout=5)

    assert failures == []
    assert second_outcome is PairingState.PAIRED
    assert sdk_channel.history_calls == [
        {"after": 109, "limit": 100, "oldest_first": True},
        {"after": 209, "limit": 100, "oldest_first": True},
    ]
    assert {row.external_id for row in Message._base_manager.filter(channel_id=channel.pk)} == {
        f"700/{message_id}" for message_id in range(105, 224)
    }
    with system_context(reason="test.messaging.discord.resume.verify"):
        channel.refresh_from_db()
        assert channel.subscription_state["channel_watermarks"]["700"] == "222"


@pytest.mark.django_db(transaction=True)
def test_discord_history_channels_are_ordered_by_recent_activity(
    discord_tables: Any,
    discord_session_module: ModuleType,
) -> None:
    """An older active channel ranks ahead of a newer inactive channel."""

    admin = _platform_admin("msg-discord-activity-admin")
    channel = _discord_channel(admin)
    inactive_new = _FakeDiscordChannel(900, last_message_id=901)
    active_old = _FakeDiscordChannel(700, last_message_id=1_500)
    direct = _FakeDiscordChannel(800, last_message_id=1_200)
    session = discord_session_module.DiscordSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    session.client = SimpleNamespace(
        get_all_channels=lambda: [inactive_new, active_old],
        guilds=[],
        private_channels=[direct],
    )

    assert [item.id for item in session._readable_channels()] == [700, 800, 900]


@pytest.mark.django_db(transaction=True)
def test_discord_invalid_token_reports_logged_out(
    discord_tables: Any,
    discord_session_module: ModuleType,
) -> None:
    """LoginFailure and 401-class errors prove the retained bot token is invalid."""

    admin = _platform_admin("msg-discord-invalid-admin")
    channel = _discord_channel(admin, token="invalid-bot-token")
    session = discord_session_module.DiscordSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    path = session_store_path(channel) / "session.marker"
    path.parent.mkdir(parents=True, exist_ok=True)
    session.client = session._build_client(path)

    session._connect()

    kinds = [session.events.get_nowait()[0] for _ in range(session.events.qsize())]
    assert kinds == ["logged_out", "disconnected"]
    assert session._is_logged_out(_FakeHTTPException(401)) is True
    assert session._is_logged_out(_FakeHTTPException(500)) is False


@pytest.mark.django_db(transaction=True)
def test_discord_media_download_uses_capped_signed_url_without_bearer(
    discord_tables: Any,
    discord_session_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The task thread schedules signed-CDN download on the running vendor loop."""

    admin = _platform_admin("msg-discord-media-admin")
    channel = _discord_channel(admin)
    channel.config = {"max_media_bytes": 5}
    session = discord_session_module.DiscordSession(
        channel,
        reporter=BridgeProgressReporter(channel),
        stop_event=threading.Event(),
    )
    calls: list[tuple[str, int]] = []

    def download(url: str, *, cap: int) -> bytes:
        calls.append((url, cap))
        return b"image"

    monkeypatch.setattr(session.http, "download_capped", download)
    fact = _discord_module("identity").DiscordMediaFact(
        url="https://cdn.discordapp.com/attachments/a/b/photo.jpg?ex=1&is=2&hm=3",
        mime="image/jpeg",
        name="photo.jpg",
        size=5,
    )

    loop = asyncio.new_event_loop()
    loop_started = threading.Event()
    loop_thread_ids: list[int] = []
    coroutine_thread_ids: list[int] = []
    original_download = session._download_coro

    async def tracked_download(payload: Any, media_fact: Any) -> bytes | None:
        coroutine_thread_ids.append(threading.get_ident())
        return await original_download(payload, media_fact)

    monkeypatch.setattr(session, "_download_coro", tracked_download)
    session._loop = loop

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop_thread_ids.append(threading.get_ident())
        loop_started.set()
        loop.run_forever()
        loop.close()

    loop_thread = threading.Thread(target=run_loop, daemon=True)
    loop_thread.start()
    assert loop_started.wait(timeout=5)
    try:
        content = session._download(None, fact)
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=5)

    assert content == b"image"
    assert not loop_thread.is_alive()
    assert coroutine_thread_ids == loop_thread_ids
    assert calls == [
        (
            "https://cdn.discordapp.com/attachments/a/b/photo.jpg?ex=1&is=2&hm=3",
            5,
        )
    ]
