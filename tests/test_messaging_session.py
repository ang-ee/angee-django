"""The asyncio live-session frame owns its loop lifetime, including vendor leftovers."""

from __future__ import annotations

import asyncio
from queue import Queue
from types import SimpleNamespace
from typing import Any

from angee.messaging.session import AsyncioLiveSession


def test_asyncio_session_drains_vendor_tasks_after_a_failed_teardown() -> None:
    """A background task still finishes its cleanup when the vendor's loop teardown raises."""

    events: list[str] = []

    class Session(AsyncioLiveSession):
        async def _run_client(self) -> None:
            async def background() -> None:
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("background-finished")

            asyncio.get_running_loop().create_task(background())
            await asyncio.sleep(0)

        async def _download_coro(self, payload: Any, fact: Any) -> None:
            return None

        def _is_logged_out(self, error: Exception) -> bool:
            return False

        def _teardown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            events.append("vendor-teardown")
            raise RuntimeError("vendor teardown failed")

        def _stop_main(self, loop: asyncio.AbstractEventLoop, *, deadline: float) -> None:
            return None

    session = object.__new__(Session)
    session.bridge = SimpleNamespace(sqid="session-drain")
    session.live_impl = SimpleNamespace(label="Test")
    session.events = Queue()
    session._connect()

    assert events == ["vendor-teardown", "background-finished"]
    assert session._loop is not None and session._loop.is_closed()
    assert session.events.get_nowait() == ("disconnected", None)
