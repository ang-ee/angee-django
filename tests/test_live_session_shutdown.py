"""Deterministic thread-exit ordering at the shared live-session owner."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from angee.integrate.live import PairingState
from angee.integrate.session import LiveSession


class _ExitDuringPairingSession(LiveSession):
    """Finish the vendor thread while the task thread handles its paired event."""

    def __init__(self, *, requested_stop: bool) -> None:
        bridge = SimpleNamespace(
            live_impl=SimpleNamespace(key="fake", label="Fake", state_identity_key="own_id"),
            subscription_state={},
            sqid="fixture",
        )
        super().__init__(bridge, reporter=SimpleNamespace(), stop_event=threading.Event())  # type: ignore[arg-type]
        self.requested_stop = requested_stop
        self.release_connection = threading.Event()
        self.vendor_thread: threading.Thread | None = None

    def _build_client(self, store: Path) -> object:
        return object()

    def _report(self, state: PairingState, **pairing: Any) -> None:
        pass

    def _connect(self) -> None:
        self.vendor_thread = threading.current_thread()
        self.events.put(("paired", "account-1"))
        assert self.release_connection.wait(timeout=2)

    def _mark_paired(self, external_id: str) -> bool:
        self.pairing = PairingState.PAIRED
        if self.requested_stop:
            self.stop_event.set()
        self.release_connection.set()
        assert self.vendor_thread is not None
        self.vendor_thread.join(timeout=2)
        assert not self.vendor_thread.is_alive()
        return True

    def _shutdown(self, connection: threading.Thread) -> bool:
        self.release_connection.set()
        connection.join(timeout=2)
        return not connection.is_alive()


@pytest.mark.parametrize("requested_stop", [True, False])
def test_live_session_exit_during_pairing_respects_stop_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested_stop: bool,
) -> None:
    """A cooperative stop succeeds; the same unrequested thread exit errors."""

    monkeypatch.setattr("angee.integrate.session.session_store_path", lambda _bridge: tmp_path)
    session = _ExitDuringPairingSession(requested_stop=requested_stop)

    if requested_stop:
        assert session.run() is PairingState.PAIRED
    else:
        with pytest.raises(ConnectionError, match="Fake connection ended unexpectedly"):
            session.run()

    assert session.store_released is True
    assert session._stopping.is_set()
    assert session.vendor_thread is not None
    assert not session.vendor_thread.is_alive()


def test_asyncio_vendor_failure_queues_error_after_pairing() -> None:
    """The native asyncio producer preserves failure ordering for the task drain."""

    from angee.messaging.session import AsyncioLiveSession

    error = ConnectionError("Vendor receive failed.")

    class FailedSession(AsyncioLiveSession):
        async def _run_client(self) -> None:
            self.events.put(("paired", "account-1"))
            raise error

        async def _download_coro(self, payload: Any, fact: Any) -> bytes | None:
            return None

        def _is_logged_out(self, error: Exception) -> bool:
            return False

        def _teardown_loop(self, loop: asyncio.AbstractEventLoop) -> None:
            pass

        def _stop_main(self, loop: asyncio.AbstractEventLoop, *, deadline: float) -> None:
            pass

    bridge = SimpleNamespace(
        live_impl=SimpleNamespace(key="fake", label="Fake", state_identity_key="own_id"),
        subscription_state={},
        sqid="fixture",
    )
    session = FailedSession(bridge, reporter=SimpleNamespace(), stop_event=threading.Event())  # type: ignore[arg-type]
    session._connect()

    assert session.outcome_error is None
    assert session.events.get_nowait() == ("paired", "account-1")
    assert session.events.get_nowait() == ("error", error)
    assert session.events.get_nowait() == ("disconnected", None)
    assert session.events.empty()
