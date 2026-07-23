"""Live Telegram session — the asyncio Telethon binding, worker-only."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyInvalidError,
    AuthKeyPermEmptyError,
    AuthKeyUnregisteredError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
    SessionRevokedError,
)

from angee.messaging.backends import ParsedMessage
from angee.messaging.session import (
    API_TIMEOUT_SECONDS,
    INGEST_CHUNK,
    INITIAL_CONVERSATION_LIMIT,
    INITIAL_HISTORY_LIMIT,
    INITIAL_HISTORY_TIMEOUT_SECONDS,
    AsyncioLiveSession,
)
from angee.messaging_integrate_telegram.connect import telegram_app_keys
from angee.messaging_integrate_telegram.identity import parsed_message

logger = logging.getLogger(__name__)

QR_WAIT_SECONDS = 10.0
"""Maximum event-loop wait slice within one QR token's real lifetime."""

QR_ROTATION_LIMIT = 6
"""Maximum QR tokens presented in one pairing run (roughly a few minutes)."""

CONNECTION_WAKE_SECONDS = 1.0
"""Cooperative-stop polling interval while a connected client is idle."""

_LOGGED_OUT_ERRORS = (
    AuthKeyInvalidError,
    AuthKeyPermEmptyError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
)
"""Telethon authorization failures proving a retained device key is unusable."""


class TelegramSession(AsyncioLiveSession):
    """One Telegram connection whose vendor loop stays on its connection thread."""

    session_file_name = "session.session"
    client_class: type[Any] = TelegramClient
    new_message_event: type[Any] = events.NewMessage

    def _build_client(self, store: Path) -> Any:
        """Build Telethon from freshly revealed per-channel application keys."""

        credential = self._fresh_credential()
        if credential is None:
            raise ValueError("This Telegram channel has no application-key credential.")
        api_id, api_hash = telegram_app_keys(credential)
        client = self.client_class(
            session=str(store.with_suffix("")),
            api_id=api_id,
            api_hash=api_hash,
        )
        client.add_event_handler(self._on_new_message, self.new_message_event())
        return client

    async def _run_client(self) -> None:
        """Connect, pair if needed, seed bounded history, then await disconnect."""

        await self._bounded(self.client.connect())
        authorized = bool(await self._bounded(self.client.is_user_authorized()))
        if authorized:
            user = await self._bounded(self.client.get_me())
        else:
            if not self.created_store:
                self.events.put(("logged_out", None))
                return
            user = await self._pair()
        if user is None or self._vendor_stopping():
            return
        self._queue_account(user)
        if not self.bridge.subscription_state.get("history_seeded"):
            if await self._initial_history():
                self.events.put(("history_seeded", None))
        await self._wait_until_disconnected()

    async def _pair(self) -> Any | None:
        """Rotate bounded QR waits until login, password input, or cooperative stop."""

        qr_login = await self._bounded(self.client.qr_login())
        for round_index in range(QR_ROTATION_LIMIT):
            if self._vendor_stopping():
                return None
            self.events.put(("qr", str(qr_login.url).encode("utf-8")))
            while not self._vendor_stopping():
                remaining = self._qr_seconds_remaining(qr_login)
                if remaining <= 0:
                    break
                try:
                    return await qr_login.wait(timeout=min(remaining, QR_WAIT_SECONDS))
                except TimeoutError:
                    if self._qr_seconds_remaining(qr_login) > 0:
                        continue
                    break
                except SessionPasswordNeededError:
                    return await self._sign_in_with_password()
            if round_index + 1 < QR_ROTATION_LIMIT and not self._vendor_stopping():
                await self._bounded(qr_login.recreate())
        if not self._vendor_stopping():
            self.events.put(("pairing_timeout", None))
        return None

    @staticmethod
    def _qr_seconds_remaining(qr_login: Any) -> float:
        """Return seconds until Telethon says the current QR token expires."""

        expires = qr_login.expires
        now = datetime.now(tz=expires.tzinfo) if expires.tzinfo is not None else datetime.now()
        return max(0.0, (expires - now).total_seconds())

    async def _sign_in_with_password(self) -> Any | None:
        """Repeat bounded two-step password rounds until Telethon accepts or stops."""

        message = "Enter your Telegram two-step verification password."
        while not self._vendor_stopping():
            password = await self.request_password_async(message)
            if password is None:
                return None
            try:
                return await self._bounded(self.client.sign_in(password=password))
            except PasswordHashInvalidError:
                message = "Incorrect Telegram two-step verification password. Try again."
        return None

    async def _initial_history(self) -> bool:
        """Queue the bounded, evenly shared first history seed through normal ingest.

        Each recent dialog gets at most its share of the global message budget,
        while the global count and wall-clock deadline remain authoritative. A
        deeper ``iter_messages()`` backfill is a deliberate follow-up.
        """

        batch: list[tuple[ParsedMessage, Any]] = []
        remaining = INITIAL_HISTORY_LIMIT
        per_dialog = max(1, INITIAL_HISTORY_LIMIT // INITIAL_CONVERSATION_LIMIT)
        completed = True
        try:
            async with asyncio.timeout(INITIAL_HISTORY_TIMEOUT_SECONDS):
                dialogs = await self.client.get_dialogs(limit=INITIAL_CONVERSATION_LIMIT)
                for dialog in dialogs:
                    if remaining <= 0 or self._vendor_stopping():
                        break
                    newest = [
                        message
                        async for message in self.client.iter_messages(
                            dialog.entity,
                            limit=min(remaining, per_dialog),
                            reverse=False,
                        )
                    ]
                    for message in reversed(newest):
                        queued = self._queued_message(message, chat=getattr(dialog, "entity", None))
                        if queued is not None:
                            batch.append(queued)
                            remaining -= 1
                            if len(batch) == INGEST_CHUNK:
                                self.events.put(("messages", batch))
                                batch = []
                        if remaining <= 0:
                            break
        except TimeoutError:
            completed = False
            logger.info("Telegram initial history for channel %s reached its bound.", self.bridge.sqid)
        if batch:
            self.events.put(("messages", batch))
        return completed and not self._vendor_stopping()

    async def _wait_until_disconnected(self) -> None:
        """Wake on a short bound until Telethon disconnects or shutdown is requested."""

        disconnected = self.client.disconnected
        while not self._vendor_stopping():
            done, _pending = await asyncio.wait(
                (disconnected,),
                timeout=CONNECTION_WAKE_SECONDS,
            )
            if disconnected in done:
                await disconnected
                return

    def _queue_account(self, user: Any) -> None:
        """Queue the stable id through generic pairing, then mutable label facts."""

        own_id = str(getattr(user, "id", "") or "")
        self.events.put(("paired", own_id))
        self.events.put(
            (
                "account",
                (
                    own_id,
                    str(getattr(user, "phone", "") or ""),
                    str(getattr(user, "username", "") or ""),
                ),
            )
        )

    async def _on_new_message(self, event: Any) -> None:
        """Translate one Telethon update and enqueue it without touching the DB."""

        message = getattr(event, "message", None)
        if message is None:
            return
        queued = self._queued_message(
            message,
            chat=getattr(event, "chat", None),
            chat_id=getattr(event, "chat_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            is_group=bool(getattr(event, "is_group", False)),
            is_channel=bool(getattr(event, "is_channel", False)),
        )
        if queued is not None:
            self.events.put(("messages", [queued]))

    def _queued_message(
        self,
        message: Any,
        *,
        chat: Any | None = None,
        chat_id: Any | None = None,
        is_private: bool | None = None,
        is_group: bool | None = None,
        is_channel: bool | None = None,
    ) -> tuple[ParsedMessage, Any] | None:
        """Return one neutral message plus its media download payload."""

        resolved_chat_id = chat_id if chat_id is not None else getattr(message, "chat_id", None)
        if resolved_chat_id is None or not getattr(message, "id", None):
            return None
        has_media = getattr(message, "media", None) is not None
        text = getattr(message, "raw_text", None) or getattr(message, "message", None)
        if not text and not has_media:
            return None
        parsed = parsed_message(
            message,
            chat_id=resolved_chat_id,
            sender_id=getattr(message, "sender_id", ""),
            sender=getattr(message, "sender", None),
            chat=chat or getattr(message, "chat", None),
            is_private=bool(getattr(message, "is_private", False) if is_private is None else is_private),
            is_group=bool(getattr(message, "is_group", False) if is_group is None else is_group),
            is_channel=bool(getattr(message, "is_channel", False) if is_channel is None else is_channel),
        )
        return parsed, message if has_media else None

    def _handle(self, kind: str, payload: Any) -> bool:
        """Persist account labels on the task thread; delegate message ingest."""

        if kind == "account":
            own_id, phone, username = payload
            self.live_impl.remember_account_profile(
                own_id,
                phone=phone,
                username=username,
            )
            self._report(self.pairing)
            return self._still_wanted()
        if kind == "pairing_timeout":
            self.pairing = self.pairing.STOPPED
            self._report(self.pairing)
            return False
        return super()._handle(kind, payload)

    async def _download_coro(self, payload: Any, _fact: Any) -> bytes | None:
        """Download Telethon media from inside the owning vendor loop."""

        if payload is None:
            return None
        content = await self.client.download_media(payload, file=bytes)
        return bytes(content) if isinstance(content, bytes | bytearray) else None

    def _is_logged_out(self, error: Exception) -> bool:
        """Classify Telethon's retained-authorization failure types."""

        return isinstance(error, _LOGGED_OUT_ERRORS)

    def _teardown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Disconnect Telethon before its owning event loop closes."""

        try:
            if not loop.is_closed():
                loop.run_until_complete(self._disconnect_client())
        except Exception:
            logger.info("Telegram disconnect for channel %s failed.", self.bridge.sqid)

    async def _disconnect_client(self) -> None:
        """Disconnect Telethon from inside its owning event loop."""

        if self.client is None:
            return
        result = self.client.disconnect()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=API_TIMEOUT_SECONDS)

    def _stop_main(self, loop: asyncio.AbstractEventLoop, *, deadline: float) -> None:
        """Synchronously disconnect Telethon within the shared stop deadline."""

        future = None
        disconnect = self._disconnect_client()
        try:
            future = asyncio.run_coroutine_threadsafe(disconnect, loop)
            future.result(timeout=max(0.0, deadline - time.monotonic()))
        except Exception:
            if future is not None:
                future.cancel()
            else:
                disconnect.close()
            logger.info("Stopping the Telegram client for channel %s failed.", self.bridge.sqid)
