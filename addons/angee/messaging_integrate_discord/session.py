"""Live Discord bot Gateway session — discord.py, worker-only."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import discord

from angee.messaging.backends import ParsedMessage
from angee.messaging.session import (
    API_TIMEOUT_SECONDS,
    INGEST_CHUNK,
    INITIAL_CONVERSATION_LIMIT,
    INITIAL_HISTORY_LIMIT,
    INITIAL_HISTORY_TIMEOUT_SECONDS,
    AsyncioLiveSession,
)
from angee.messaging_integrate_discord.connect import discord_bot_token
from angee.messaging_integrate_discord.identity import DiscordMediaFact, parsed_message

logger = logging.getLogger(__name__)

_DEFAULT_MAX_MEDIA_BYTES = 50_000_000
_SESSION_FILE = "session.marker"


class DiscordSession(AsyncioLiveSession):
    """One bot token whose discord.py loop belongs to its connection thread."""

    session_file_name = _SESSION_FILE
    client_class: type[Any] = discord.Client

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._token = ""
        self._history_running = False
        self.http = self.live_impl.http

    def _build_client(self, store: Path) -> Any:
        """Build a minimal-intent Discord client and retain a store marker."""

        credential = self._fresh_credential()
        if credential is None:
            raise ValueError("This Discord channel has no bot-token credential.")
        self._token = discord_bot_token(credential)
        store.touch(exist_ok=True)
        store.chmod(0o600)
        intents = discord.Intents(
            guilds=True,
            guild_messages=True,
            message_content=True,
            dm_messages=True,
        )
        return self.client_class(intents=intents)

    async def _run_client(self) -> None:
        """Register Gateway handlers, authenticate the bot, and run forever."""

        self._register_handlers()
        await self.client.start(self._token)

    def _register_handlers(self) -> None:
        """Register the Discord events owned by this ingestion session."""

        async def on_ready() -> None:
            await self._on_ready()

        async def on_message(message: Any) -> None:
            self._queue_one(message)

        async def on_message_edit(_before: Any, after: Any) -> None:
            self._queue_one(after)

        async def on_message_delete(message: Any) -> None:
            self._queue_one(message, deleted=True)

        async def on_bulk_message_delete(messages: list[Any]) -> None:
            self._queue_many(messages, deleted=True)

        for handler in (
            on_ready,
            on_message,
            on_message_edit,
            on_message_delete,
            on_bulk_message_delete,
        ):
            self.client.event(handler)

    async def _on_ready(self) -> None:
        """Claim the bot account, persist its label, and run bounded catch-up."""

        user = self.client.user
        own_id = str(getattr(user, "id", "") or "")
        if not own_id:
            raise RuntimeError("Discord Gateway ready event has no bot user.")
        username = str(getattr(user, "name", "") or getattr(user, "username", "") or "")
        self.events.put(("paired", own_id))
        self.events.put(("account", (own_id, username)))
        if self._history_running or self._vendor_stopping():
            return
        self._history_running = True
        try:
            initial = not bool(self.bridge.subscription_state.get("history_seeded"))
            completed = await self._history_catch_up()
            if initial and completed:
                self.events.put(("history_seeded", None))
        finally:
            self._history_running = False

    async def _history_catch_up(self) -> bool:
        """Seed recent channels or resume each saved REST frontier to present."""

        remaining = INITIAL_HISTORY_LIMIT
        per_channel = max(1, INITIAL_HISTORY_LIMIT // INITIAL_CONVERSATION_LIMIT)
        watermarks = self.bridge.subscription_state.get("channel_watermarks")
        saved = watermarks if isinstance(watermarks, Mapping) else {}
        completed = True
        try:
            async with asyncio.timeout(INITIAL_HISTORY_TIMEOUT_SECONDS):
                for channel in self._readable_channels()[:INITIAL_CONVERSATION_LIMIT]:
                    if self._vendor_stopping():
                        break
                    channel_id = str(getattr(channel, "id", "") or "")
                    if not channel_id:
                        continue
                    watermark = str(saved.get(channel_id) or "")
                    try:
                        if watermark.isdigit():
                            await self._resume_channel_history(
                                channel,
                                channel_id=channel_id,
                                watermark=watermark,
                            )
                        elif remaining > 0:
                            remaining -= await self._seed_channel_history(
                                channel,
                                channel_id=channel_id,
                                limit=min(remaining, per_channel),
                            )
                    except _discord_exception_types("Forbidden", "NotFound"):
                        logger.info(
                            "Discord history for channel %s is no longer readable by bot %s.",
                            channel_id,
                            self.bridge.sqid,
                        )
        except TimeoutError:
            completed = False
            logger.info("Discord history for channel %s reached its bound.", self.bridge.sqid)
        return completed and not self._vendor_stopping()

    async def _seed_channel_history(
        self,
        channel: Any,
        *,
        channel_id: str,
        limit: int,
    ) -> int:
        """Queue one bounded recent baseline in chronological ingest order."""

        newest = [
            message
            async for message in channel.history(
                after=None,
                limit=limit,
                oldest_first=False,
            )
        ]
        if not newest:
            return 0
        return self._queue_history_page(channel_id, list(reversed(newest)))

    async def _resume_channel_history(
        self,
        channel: Any,
        *,
        channel_id: str,
        watermark: str,
    ) -> None:
        """Scan every ascending page after one committed contiguous frontier."""

        after_id = int(watermark)
        while not self._vendor_stopping():
            page = [
                message
                async for message in channel.history(
                    after=discord.Object(id=after_id),
                    limit=INGEST_CHUNK,
                    oldest_first=True,
                )
            ]
            if not page:
                return
            next_id = int(getattr(page[-1], "id"))
            if next_id <= after_id:
                raise RuntimeError(f"Discord history for channel {channel_id} did not advance its snowflake.")
            self._queue_history_page(channel_id, page)
            after_id = next_id
            if len(page) < INGEST_CHUNK:
                return

    def _queue_history_page(self, channel_id: str, messages: list[Any]) -> int:
        """Queue one REST page, then its frontier checkpoint after all messages."""

        batch: list[tuple[ParsedMessage, Any]] = []
        queued_count = 0
        for message in messages:
            queued = self._queued_message(message)
            if queued is None:
                continue
            batch.append(queued)
            queued_count += 1
            if len(batch) == INGEST_CHUNK:
                self.events.put(("messages", batch))
                batch = []
        if batch:
            self.events.put(("messages", batch))
        watermark = str(getattr(messages[-1], "id", "") or "")
        if not watermark.isdigit():
            raise ValueError(f"Discord history for channel {channel_id} returned no snowflake.")
        self.events.put(("history_watermark", (channel_id, watermark)))
        return queued_count

    def _readable_channels(self) -> list[Any]:
        """Return readable channels ordered by most recent visible activity."""

        channels: dict[str, Any] = {}
        get_all_channels = getattr(self.client, "get_all_channels", None)
        if callable(get_all_channels):
            for channel in get_all_channels():
                self._remember_readable_channel(channels, channel)
        for guild in getattr(self.client, "guilds", ()) or ():
            for channel in getattr(guild, "threads", ()) or ():
                self._remember_readable_channel(channels, channel)
        for channel in getattr(self.client, "private_channels", ()) or ():
            self._remember_readable_channel(channels, channel)
        return sorted(
            channels.values(),
            key=lambda channel: (
                int(getattr(channel, "last_message_id", 0) or 0),
                int(getattr(channel, "id", 0) or 0),
            ),
            reverse=True,
        )

    @staticmethod
    def _remember_readable_channel(channels: dict[str, Any], channel: Any) -> None:
        """Add one channel when discord.py exposes readable message history."""

        channel_id = str(getattr(channel, "id", "") or "")
        if not channel_id or not callable(getattr(channel, "history", None)):
            return
        guild = getattr(channel, "guild", None)
        permissions_for = getattr(channel, "permissions_for", None)
        member = getattr(guild, "me", None)
        if guild is not None and member is not None and callable(permissions_for):
            permissions = permissions_for(member)
            if not bool(getattr(permissions, "view_channel", True)) or not bool(
                getattr(permissions, "read_message_history", True)
            ):
                return
        channels[channel_id] = channel

    def _queue_one(self, message: Any, *, deleted: bool = False) -> None:
        """Translate one Gateway message and enqueue it without touching Django."""

        queued = self._queued_message(message, deleted=deleted)
        if queued is not None:
            self.events.put(("messages", [queued]))

    def _queue_many(self, messages: list[Any], *, deleted: bool = False) -> None:
        """Translate a bulk Gateway event into bounded ingest chunks."""

        batch: list[tuple[ParsedMessage, Any]] = []
        for message in messages:
            queued = self._queued_message(message, deleted=deleted)
            if queued is None:
                continue
            batch.append(queued)
            if len(batch) == INGEST_CHUNK:
                self.events.put(("messages", batch))
                batch = []
        if batch:
            self.events.put(("messages", batch))

    def _queued_message(self, message: Any, *, deleted: bool = False) -> tuple[ParsedMessage, Any] | None:
        """Return one neutral Discord message and its signed-CDN payload."""

        wire = _message_mapping(message)
        if deleted:
            wire["deleted"] = True
        parsed = parsed_message(wire, own_id=self.own_id or getattr(self.client.user, "id", ""))
        return (parsed, wire) if parsed is not None else None

    def _handle(self, kind: str, payload: Any) -> bool:
        """Persist bot labels and committed REST frontiers on the task thread."""

        if kind == "account":
            own_id, username = payload
            self.live_impl.remember_account_profile(own_id, username=username)
            self._report(self.pairing)
            return self._still_wanted()
        if kind == "history_watermark":
            channel_id, watermark = payload
            raw = self.bridge.subscription_state.get("channel_watermarks")
            updated = {str(key): str(value) for key, value in raw.items()} if isinstance(raw, Mapping) else {}
            previous = updated.get(channel_id, "")
            if not previous.isdigit() or int(watermark) > int(previous):
                updated[channel_id] = watermark
                self.bridge.merge_subscription_state(channel_watermarks=updated)
            return self._still_wanted()
        return super()._handle(kind, payload)

    async def _download_coro(self, _payload: Any, fact: DiscordMediaFact) -> bytes | None:
        """Fetch one signed CDN URL without adding a bearer credential."""

        cap = self._max_media_bytes()
        if fact.size > cap:
            return None
        return await asyncio.to_thread(
            self.http.download_capped,
            fact.url,
            cap=cap,
        )

    def _max_media_bytes(self) -> int:
        """Return this channel's positive per-attachment download cap."""

        config = self.bridge.config if isinstance(self.bridge.config, Mapping) else {}
        try:
            return max(1, int(config.get("max_media_bytes") or _DEFAULT_MAX_MEDIA_BYTES))
        except (TypeError, ValueError):  # fmt: skip
            return _DEFAULT_MAX_MEDIA_BYTES

    def _is_logged_out(self, error: Exception) -> bool:
        """Classify Discord authentication failures that void the bot token."""

        if isinstance(error, _discord_exception_types("LoginFailure")):
            return True
        return getattr(error, "status", None) == 401 or getattr(error, "code", None) == 4004

    def _teardown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Close Discord HTTP/Gateway resources and cancel remaining loop tasks."""

        try:
            if not loop.is_closed():
                loop.run_until_complete(self._close_client())
        except Exception:
            logger.info("Discord close for channel %s failed.", self.bridge.sqid)
        pending = asyncio.all_tasks(loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        asyncio.set_event_loop(None)

    async def _close_client(self) -> None:
        """Let discord.py close its Gateway and HTTP resources cleanly."""

        if self.client is not None and not bool(getattr(self.client, "is_closed", lambda: False)()):
            await asyncio.wait_for(self.client.close(), timeout=API_TIMEOUT_SECONDS)

    def _stop_main(self, loop: asyncio.AbstractEventLoop, *, deadline: float) -> None:
        """Synchronously close discord.py within the shared shutdown deadline.

        ``Client.close`` closes the Gateway and its HTTP session, which makes
        ``Client.start`` return naturally; cancelling the main task first would
        bypass that SDK-owned cleanup.
        """

        future = None
        close = self._close_client()
        try:
            future = asyncio.run_coroutine_threadsafe(close, loop)
            future.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            if future is not None:
                future.cancel()
            else:
                close.close()
            logger.info("Stopping the Discord client for channel %s failed.", self.bridge.sqid)


def _message_mapping(message: Any) -> dict[str, Any]:
    """Serialize a discord.py Message into the console-safe identity boundary."""

    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None) or getattr(channel, "guild", None)
    author = getattr(message, "author", None)
    reference = getattr(message, "reference", None)
    created_at = getattr(message, "created_at", None)
    timestamp_ms = int(created_at.timestamp() * 1000) if isinstance(created_at, datetime) else 0
    message_type = getattr(message, "type", 0)
    return {
        "id": str(getattr(message, "id", "") or ""),
        "type": getattr(message_type, "value", message_type),
        "content": str(getattr(message, "content", "") or ""),
        "timestamp_ms": timestamp_ms,
        "guild_id": str(getattr(guild, "id", "") or ""),
        "channel": {
            "id": str(getattr(channel, "id", "") or ""),
            "name": str(getattr(channel, "name", "") or ""),
            "guild": (
                None
                if guild is None
                else {
                    "id": str(getattr(guild, "id", "") or ""),
                    "name": str(getattr(guild, "name", "") or ""),
                }
            ),
        },
        "author": {
            "id": str(getattr(author, "id", "") or ""),
            "username": str(getattr(author, "name", "") or getattr(author, "username", "") or ""),
            "global_name": str(getattr(author, "global_name", "") or ""),
        },
        "attachments": [
            {
                "url": str(getattr(attachment, "url", "") or ""),
                "filename": str(getattr(attachment, "filename", "") or ""),
                "content_type": str(getattr(attachment, "content_type", "") or ""),
                "size": getattr(attachment, "size", 0),
            }
            for attachment in getattr(message, "attachments", ()) or ()
        ],
        "message_reference": (
            None
            if reference is None
            else {
                "message_id": str(getattr(reference, "message_id", "") or ""),
                "channel_id": str(getattr(reference, "channel_id", "") or ""),
            }
        ),
    }


def _discord_exception_types(*names: str) -> tuple[type[BaseException], ...]:
    """Return available discord.py exception classes by public name."""

    return tuple(
        value
        for name in names
        if isinstance((value := getattr(discord, name, None)), type) and issubclass(value, BaseException)
    )
