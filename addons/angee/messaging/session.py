"""Worker-only live channel ingestion.

This module imports :mod:`angee.integrate.session`, whose worker loop imports
``qrcode``. The console must never load either worker module; console-safe
backend declarations stay in :mod:`angee.messaging.backends`, mirroring the
``integrate.live`` / ``integrate.session`` split.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import replace
from typing import Any, Literal, TypeVar, cast, overload

from django.apps import apps

from angee.integrate.live import STOP_JOIN_SECONDS
from angee.integrate.session import LiveSession, PasswordSkipped

INGEST_CHUNK = 100
"""Messages per drain slice so cooperative-stop and lock checks stay responsive."""

API_TIMEOUT_SECONDS = 30.0
"""Maximum wait for one bounded asyncio vendor API operation."""

DOWNLOAD_TIMEOUT_SECONDS = 60.0
"""Maximum task-thread wait for one vendor-loop media download."""

INITIAL_HISTORY_TIMEOUT_SECONDS = 60.0
"""Wall-clock bound for a live channel's first bounded history seed."""

INITIAL_HISTORY_LIMIT = 100
"""Global maximum number of messages in a live channel's first history seed."""

INITIAL_CONVERSATION_LIMIT = 20
"""Maximum recent conversations considered by a first history seed."""

logger = logging.getLogger(__name__)
_T = TypeVar("_T")


class LiveChannelSession(LiveSession):
    """Live worker session that owns queued message media resolution and landing."""

    def _handle(self, kind: str, payload: Any) -> bool:
        """Land messaging batches and ignore other implementation-specific events."""

        if kind == "messages":
            return self._ingest(payload)
        return self._still_wanted()

    def _ingest(self, batch: list[tuple[Any, Any]]) -> bool:
        """Land one queued batch in bounded chunks, checking liveness between them."""

        message_model = apps.get_model("messaging", "Message")
        for start in range(0, len(batch), INGEST_CHUNK):
            chunk = batch[start : start + INGEST_CHUNK]
            parsed = [
                self.live_impl.parse_live_message(self._with_media(message, payload)) for message, payload in chunk
            ]
            landed = message_model.objects.ingest(
                parsed,
                channel=self.bridge,
                quote_edges=self.live_impl.quote_edges,
            )
            self._after_ingest(chunk, landed)
            self.landed += len(landed)
            self._report(self.pairing)
            if start + INGEST_CHUNK < len(batch) and not self._still_wanted():
                return False
        return self._still_wanted()

    def _after_ingest(self, _batch: list[tuple[Any, Any]], _landed: list[Any]) -> None:
        """Run implementation cleanup only after the owning messages commit."""

    def _with_media(self, message: Any, payload: Any) -> Any:
        """Resolve queued media facts through the implementation's declared DTO class."""

        metadata = dict(message.metadata)
        facts = metadata.pop("_media_facts", ())
        if not facts:
            return message
        media_item_class = self.live_impl.media_item_class
        media = tuple(
            media_item_class(
                mime=getattr(fact, "mime", "application/octet-stream"),
                name=getattr(fact, "name", ""),
                content=self._download(payload, fact),
            )
            for fact in facts
        )
        return self._attach_media(replace(message, metadata=metadata), media)

    def _attach_media(self, message: Any, media: tuple[Any, ...]) -> Any:
        """Attach resolved media to the default vendor DTO shape."""

        return message.with_media(media)


class AsyncioLiveSession(LiveChannelSession, ABC):
    """Live channel session whose vendor asyncio loop owns its connection thread.

    This deliberately refines :class:`LiveChannelSession`, not integrate's
    ``LiveSession``: the asyncio frame currently has only messaging-channel
    consumers; a non-messaging asyncio bridge would lift it to integrate.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._main_task: asyncio.Task[Any] | None = None

    def _connect(self) -> None:
        """Own and run the vendor asyncio loop on the connection thread."""

        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            self._main_task = loop.create_task(self._run_client())
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            if self._is_logged_out(error):
                self.events.put(("logged_out", None))
            else:
                self.outcome_error = error
                logger.exception(
                    "%s connection for channel %s crashed.",
                    self.live_impl.label,
                    self.bridge.sqid,
                )
        finally:
            try:
                if not loop.is_closed():
                    self._teardown_loop(loop)
            except Exception:
                logger.info(
                    "%s teardown for channel %s did not finish cleanly.",
                    self.live_impl.label,
                    self.bridge.sqid,
                )
            finally:
                if not loop.is_closed():
                    loop.close()
                self.events.put(("disconnected", None))

    def _shutdown(self, connection: threading.Thread) -> bool:
        """Stop the vendor main task and join its thread within one shared bound."""

        deadline = time.monotonic() + STOP_JOIN_SECONDS
        loop = self._loop
        if loop is not None and not loop.is_closed() and loop.is_running():
            self._stop_main(loop, deadline=deadline)
        connection.join(timeout=max(0.0, deadline - time.monotonic()))
        return not connection.is_alive()

    async def _bounded(self, awaitable: Awaitable[_T]) -> _T:
        """Await one vendor operation with the shared finite API bound."""

        return await asyncio.wait_for(awaitable, timeout=API_TIMEOUT_SECONDS)

    @overload
    async def request_password_async(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: Literal[False] = False,
    ) -> str | None: ...

    @overload
    async def request_password_async(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: Literal[True],
    ) -> str | PasswordSkipped | None: ...

    async def request_password_async(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: bool = False,
    ) -> str | PasswordSkipped | None:
        """Wait for transient operator input without blocking the vendor loop."""

        request_password = cast(
            Callable[..., str | PasswordSkipped | None],
            self.request_password,
        )
        if material_key == "password" and not optional:
            return await asyncio.to_thread(request_password, message)
        return await asyncio.to_thread(
            request_password,
            message,
            material_key=material_key,
            optional=optional,
        )

    def _run_coro_threadsafe(self, coro: Coroutine[Any, Any, _T], timeout: float) -> _T:
        """Run ``coro`` on the owning vendor loop and wait for a finite result."""

        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            coro.close()
            raise RuntimeError("The vendor event loop is unavailable.")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except Exception:
            future.cancel()
            raise

    def _handle(self, kind: str, payload: Any) -> bool:
        """Persist the shared initial-history gate; delegate vendor events."""

        if kind == "history_seeded":
            self.bridge.merge_subscription_state(history_seeded=True)
            return self._still_wanted()
        return super()._handle(kind, payload)

    def _download(self, payload: Any, fact: Any) -> bytes | None:
        """Run one media coroutine on its owning loop with a finite wait."""

        loop = self._loop
        if loop is None or loop.is_closed() or not loop.is_running():
            return None
        try:
            return self._run_coro_threadsafe(
                self._download_coro(payload, fact),
                DOWNLOAD_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.info(
                "%s media download failed for channel %s.",
                self.live_impl.label,
                self.bridge.sqid,
            )
            return None

    @abstractmethod
    async def _run_client(self) -> None:
        """Run the vendor's async authenticate/ingest main."""

    @abstractmethod
    async def _download_coro(self, payload: Any, fact: Any) -> bytes | None:
        """Download one vendor media fact from inside its owning loop."""

    @abstractmethod
    def _is_logged_out(self, error: Exception) -> bool:
        """Return whether ``error`` proves retained vendor auth is unusable."""

    @abstractmethod
    def _teardown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Close vendor resources before the owning event loop closes."""

    @abstractmethod
    def _stop_main(self, loop: asyncio.AbstractEventLoop, *, deadline: float) -> None:
        """Request vendor shutdown without exceeding ``deadline``."""
