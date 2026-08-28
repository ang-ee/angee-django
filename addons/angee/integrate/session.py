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
from typing import Any, Literal, overload

import qrcode
from django.utils import timezone

from angee.integrate.live import (
    AWAITING_PASSWORD_WAKE_SECONDS,
    STOP_JOIN_SECONDS,
    WAKE_SECONDS,
    PairingState,
    is_skip_marker,
    reset_session_store,
    session_store_path,
)
from angee.integrate.locks import bridge_is_locked
from angee.integrate.models import IntegrationRuntimeStatus
from angee.integrate.sync import BridgeProgressReporter

logger = logging.getLogger(__name__)


class PasswordSkipped:
    """Type of the explicit optional-password skip sentinel."""

    __slots__ = ()


PASSWORD_SKIPPED = PasswordSkipped()
"""An optional password round was skipped; distinct from the abort value ``None``."""


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
        self.inputs: queue.Queue[str | PasswordSkipped] = queue.Queue()
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
        self._password_delivered = False
        self._password_material_key = "password"
        self._password_optional = False
        self.outcome_error: Exception | None = None
        self._stopping = threading.Event()

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
            self._stopping.set()
            try:
                self.store_released = self._shutdown(connection)
            finally:
                self._discard_inputs()
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

        wake_seconds = (
            AWAITING_PASSWORD_WAKE_SECONDS if self.pairing is PairingState.AWAITING_PASSWORD else WAKE_SECONDS
        )
        try:
            kind, payload = self.events.get(timeout=wake_seconds)
        except queue.Empty:
            return self._still_wanted()
        if kind == "qr":
            self.pairing = PairingState.AWAITING_SCAN
            self._report(PairingState.AWAITING_SCAN, qr=_qr_data_uri(payload))
        elif kind == "awaiting_password":
            message, material_key, optional = payload
            if not self._mark_awaiting_password(
                message,
                material_key=material_key,
                optional=optional,
            ):
                return False
        elif kind == "paired":
            keep_running = self._mark_paired(payload)
            return self._clear_delivered_password() and keep_running
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
        return self._deliver_password_if_ready()

    def _vendor_stopping(self) -> bool:
        """Return whether either session owner requested vendor shutdown."""

        return self._stopping.is_set() or self.stop_event.is_set()

    @overload
    def request_password(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: Literal[False] = False,
    ) -> str | None: ...

    @overload
    def request_password(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: Literal[True],
    ) -> str | PasswordSkipped | None: ...

    def request_password(
        self,
        message: str = "",
        *,
        material_key: str = "password",
        optional: bool = False,
    ) -> str | PasswordSkipped | None:
        """Ask for one transient secret, a skip sentinel, or ``None`` on stop.

        ``message`` is a non-secret, operator-visible prompt from the vendor.
        ``material_key`` selects the consume-once credential key. Optional rounds
        may be explicitly skipped without conflating that choice with shutdown.
        Vendor connections call this from their own thread. The queue wait stays
        bounded by the short awaiting-password wake so a task-thread stop decision
        can unwind a vendor blocked here without outliving cooperative shutdown.
        """

        prompt = str(message or "")
        key = str(material_key or "").strip()
        if not key:
            raise ValueError("A transient material key is required.")
        self.events.put(("awaiting_password", (prompt, key, optional)))
        stopped_states = {
            PairingState.PAUSED,
            PairingState.LOGGED_OUT,
            PairingState.STOPPED,
            PairingState.DUPLICATE_ACCOUNT,
        }
        while not self._stopping.is_set() and not self.stop_event.is_set():
            try:
                return self.inputs.get(timeout=AWAITING_PASSWORD_WAKE_SECONDS)
            except queue.Empty:
                if self.pairing in stopped_states:
                    return None
        return None

    def _mark_awaiting_password(
        self,
        message: str,
        *,
        material_key: str = "password",
        optional: bool = False,
    ) -> bool:
        """Scrub the old round and arm the awaiting-password tri-state.

        ``awaiting`` is absent before any prompt, the material key while this
        round is armed, and ``""`` after its answer is submitted or consumed.
        The merge-only state owner keeps the sentinel; a new round explicitly
        overwrites it after invalidating persisted and queued old answers.
        """

        credential = self._fresh_credential()
        if credential is None:
            return self._terminal_password_failure(ValueError("This live bridge has no credential for password input."))
        self._arm_password_round(
            message,
            credential=credential,
            material_key=material_key,
            optional=optional,
        )
        return True

    def _arm_password_round(
        self,
        message: str,
        *,
        credential: Any,
        material_key: str = "password",
        optional: bool = False,
    ) -> None:
        """Invalidate old answers, persist the armed marker, and report the prompt."""

        key = str(material_key or "").strip()
        if not key:
            raise ValueError("A transient material key is required.")
        self._discard_inputs()
        self._password_material_key = key
        self._password_optional = optional
        credential.update_material(**{key: None})
        self.bridge.merge_subscription_state(awaiting=key)
        self.pairing = PairingState.AWAITING_PASSWORD
        self._report(
            PairingState.AWAITING_PASSWORD,
            **({"message": message} if message else {}),
            **({"can_skip": True} if optional else {}),
        )

    def _deliver_password_if_ready(self) -> bool:
        """Consume the submitted arm of the awaiting-password tri-state.

        ``awaiting`` is absent before the first prompt, the material key while
        armed, and ``""`` after submit/consume. Delivery always refreshes the
        credential relation so a long-lived bridge instance cannot reuse an FK
        object whose encrypted material predates the operator's write.
        """

        if self.pairing is not PairingState.AWAITING_PASSWORD or self._password_delivered:
            return True
        material_key = self._password_material_key
        awaiting = self.bridge.subscription_state.get("awaiting")
        if is_skip_marker(awaiting, material_key):
            self._password_delivered = True
            self.inputs.put(PASSWORD_SKIPPED)
            return True
        if awaiting != "":
            return True
        credential = self._fresh_credential()
        if credential is None:
            return self._terminal_password_failure(ValueError("This live bridge has no credential for password input."))
        password = credential.reveal().get(material_key)
        if not isinstance(password, str) or not password:
            error = ValueError("The live bridge credential has no submitted password.")
            logger.error(
                "%s session for bridge %s could not consume password input: %s",
                self.live_impl.label,
                self.bridge.sqid,
                error,
            )
            self._arm_password_round(
                "The submitted password was unavailable. Enter the bridge password again.",
                credential=credential,
                material_key=material_key,
                optional=self._password_optional,
            )
            self.bridge.record_sync_error(error, now=timezone.now())
            self.outcome_error = error
            return True
        self._password_delivered = True
        self.inputs.put(password)
        return True

    def _clear_delivered_password(self) -> bool:
        """Consume the transient password after the vendor reports successful sign-in."""

        if not self._password_delivered:
            return True
        credential = self._fresh_credential()
        if credential is None:
            return self._terminal_password_failure(ValueError("This live bridge has no credential for password input."))
        credential.update_material(**{self._password_material_key: None})
        self._password_delivered = False
        return True

    def _fresh_credential(self) -> Any | None:
        """Reload and return the bridge credential, replacing Django's FK cache."""

        self.bridge.refresh_from_db(fields=["credential"])
        return self.bridge.credential

    def _terminal_password_failure(self, error: Exception) -> bool:
        """Report a safe runtime failure and end this session without raising."""

        logger.error(
            "%s session for bridge %s cannot continue password input: %s",
            self.live_impl.label,
            self.bridge.sqid,
            error,
        )
        self.outcome_error = error
        self.pairing = PairingState.STOPPED
        self._discard_inputs()
        self._report(PairingState.STOPPED)
        self.bridge.record_sync_error(error, now=timezone.now())
        return False

    def _discard_inputs(self) -> None:
        """Discard queued password answers and reset delivery for a new round/end."""

        while True:
            try:
                self.inputs.get_nowait()
            except queue.Empty:
                break
        self._password_delivered = False

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
        self.outcome_error = None
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

    def _download(self, payload: Any, fact: Any) -> bytes | None:
        """Fetch one media fact's bytes; ``None`` lands a marker part."""

        raise NotImplementedError

    def _handle(self, kind: str, payload: Any) -> bool:
        """Handle vendor-specific queued events."""

        raise NotImplementedError
