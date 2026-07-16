"""Worker-only live bridge session loop.

This module owns the long-lived task discipline shared by live bridges: QR
reporting, bounded wake checks, cooperative stop, advisory-lock liveness, and
the proof that a vendor connection released its store before deletion. It imports
``qrcode`` at module top by design, so console-safe paths import
``integrate.live`` instead.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from typing import Any

import qrcode

from angee.integrate.live import (
    STOP_JOIN_SECONDS,
    WAKE_SECONDS,
    PairingState,
    reset_session_store,
    session_store_path,
)
from angee.integrate.locks import bridge_is_locked
from angee.integrate.models import IntegrationRuntimeStatus
from angee.integrate.sync import BridgeProgressReporter

logger = logging.getLogger(__name__)


def _qr_data_uri(payload: bytes) -> str:
    """Render the pairing QR payload to a PNG data URI."""

    image = qrcode.make(payload.decode("utf-8", errors="replace"))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class LiveSession:
    """One live vendor connection: pairing, event drain, and cooperative stop."""

    session_file_name = "session.db"

    def __init__(self, bridge: Any, *, reporter: BridgeProgressReporter, stop_event: threading.Event) -> None:
        """Bind the session to one bridge row and its progress reporter."""

        self.bridge = bridge
        self.reporter = reporter
        self.stop_event = stop_event
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.client: Any = None
        self.live_impl = self.bridge.live_impl
        self.pairing = PairingState.STARTING
        self.own_id = str(self.bridge.subscription_state.get(self.live_impl.state_identity_key) or "")
        self.created_store = False
        self.landed = 0
        self.store_released = False
        self.duplicate_error: Exception | None = None
        self._account_locks = ExitStack()
        self._account_id = ""

    def run(self) -> PairingState:
        """Connect and drain events until stopped, logged out, disconnected, or unlocked."""

        store = session_store_path(self.bridge)
        device = store / self.session_file_name
        self.created_store = not device.exists()
        store.mkdir(parents=True, exist_ok=True)
        self.client = self._build_client(device)
        connection = threading.Thread(
            target=self._connect,
            name=f"{self.live_impl.key}-{self.bridge.sqid}",
            daemon=True,
        )
        self._report(PairingState.STARTING)
        connection.start()
        try:
            while True:
                if not self._drain_once():
                    break
                if not connection.is_alive():
                    raise ConnectionError(f"{self.live_impl.label} connection ended unexpectedly.")
        finally:
            self.store_released = self._shutdown(connection)
            self._account_locks.close()
        if self.pairing == PairingState.LOGGED_OUT:
            raise self.live_impl.logged_out_error()
        return self.pairing

    def discard_store(self) -> None:
        """Delete this session's store only after the vendor connection released it."""

        if not self.store_released:
            logger.warning(
                "%s session for bridge %s did not release its store within %ss; "
                "leaving it for an explicit pairing reset.",
                self.live_impl.label,
                self.bridge.sqid,
                STOP_JOIN_SECONDS,
            )
            return
        reset_session_store(self.bridge)

    def discard_new_store(self) -> None:
        """Discard the store only when this session created its pairing material.

        A duplicate rejection means another bridge owns this account. That is
        not proof this row's retained session credential is void: a disconnected
        bridge may retain its account identity and store, release the claim, and
        later resume after another bridge has claimed the same account. Deleting
        here would destroy the credential disconnect was designed to preserve.
        A session that found no store file is the only one that created what it
        would delete; the rest report the conflict and leave the store for an
        explicit pairing reset.

        The store answers that question (:attr:`created_store`), not the row's
        account claim: this path's own ``release_account`` drops the claim before
        the discard runs, so deriving it from the row would read "no prior claim"
        on the operator's second identical attempt and wipe the credential the
        first one correctly kept.
        """

        if not self.created_store:
            logger.info(
                "%s session for bridge %s was rejected while resuming a pre-existing "
                "session store; leaving it for an explicit pairing reset.",
                self.live_impl.label,
                self.bridge.sqid,
            )
            return
        self.discard_store()

    def _drain_once(self) -> bool:
        """Handle queued events for up to one wake; return whether to keep running."""

        try:
            kind, payload = self.events.get(timeout=WAKE_SECONDS)
        except queue.Empty:
            return self._still_wanted()
        if kind == "qr":
            self.pairing = PairingState.AWAITING_SCAN
            self._report(PairingState.AWAITING_SCAN, qr=_qr_data_uri(payload))
        elif kind == "paired":
            return self._mark_paired(payload)
        elif kind == "logged_out":
            self.pairing = PairingState.LOGGED_OUT
            self._report(PairingState.LOGGED_OUT)
            return False
        elif kind == "disconnected":
            return False
        else:
            return self._handle(kind, payload)
        return self._still_wanted()

    def _still_wanted(self) -> bool:
        """Check shutdown, persisted desire, lifecycle, and advisory-lock liveness."""

        if self.stop_event.is_set():
            self.pairing = PairingState.STOPPED if self.pairing != PairingState.PAIRED else self.pairing
            return False
        self.bridge.refresh_from_db(fields=["lifecycle", "subscription_state"])
        if self.bridge.subscription_state.get("desired") != self.bridge.LiveState.LIVE:
            self.pairing = PairingState.STOPPED
            return False
        lifecycle = self.bridge.Lifecycle.from_value(self.bridge.lifecycle)
        if lifecycle is self.bridge.Lifecycle.PAUSED:
            self.pairing = PairingState.PAUSED
            return False
        if lifecycle is not self.bridge.Lifecycle.CONNECTED:
            self.pairing = PairingState.STOPPED
            return False
        if not bridge_is_locked(self.bridge):
            logger.warning(
                "%s session for bridge %s lost its advisory lock; exiting for a clean restart.",
                self.live_impl.label,
                self.bridge.sqid,
            )
            return False
        return True

    def _report(self, state: PairingState, **pairing: Any) -> None:
        """Persist pairing + progress; each save streams over the bridge change feed."""

        stage = self.bridge.SyncStage.SYNCING if state == PairingState.PAIRED else self.bridge.SyncStage.DISCOVERING
        details: dict[str, Any] = {"pairing": {"state": state, **pairing}}
        if self.own_id:
            for key, value in self.live_impl.pairing_report_identity(self.own_id).items():
                details["pairing"].setdefault(key, value)
        if self.landed:
            details["items"] = self.landed
        self.reporter.report(stage, details=details)

    def _mark_paired(self, external_id: str) -> bool:
        """Record the linked account once, then let reconnects pass."""

        normalized = self.live_impl.normalize_account_id(external_id) or self.own_id
        if not normalized:
            return self._still_wanted()
        if self._account_id == normalized and self.pairing == PairingState.PAIRED:
            return self._still_wanted()
        self.own_id = normalized
        if self._account_id != normalized:
            acquired = self._account_locks.enter_context(self.live_impl.account_lock(normalized))
            if not acquired:
                return self._mark_duplicate()
            if not self.live_impl.claim_account(normalized):
                return self._mark_duplicate()
            self._account_id = normalized
        if not self._still_wanted():
            return False
        self.pairing = PairingState.PAIRED
        self._report(PairingState.PAIRED)
        self.bridge.report_status(IntegrationRuntimeStatus.OK)
        return True

    def _mark_duplicate(self) -> bool:
        """Report a rejected account without naming the bridge that owns it."""

        self.duplicate_error = self.live_impl.duplicate_account_error()
        self.pairing = PairingState.DUPLICATE_ACCOUNT
        self._report(PairingState.DUPLICATE_ACCOUNT)
        return False

    def _build_client(self, store: Path) -> Any:
        """Instantiate the vendor client against the session store."""

        raise NotImplementedError

    def _connect(self) -> None:
        """Run the blocking vendor connection."""

        raise NotImplementedError

    def _shutdown(self, connection: threading.Thread) -> bool:
        """Cancel the vendor connection and return whether it actually unwound."""

        raise NotImplementedError

    def _download(self, payload: Any) -> bytes | None:
        """Fetch one message's media bytes; ``None`` lands a marker part."""

        raise NotImplementedError

    def _handle(self, kind: str, payload: Any) -> bool:
        """Handle vendor-specific queued events."""

        raise NotImplementedError
