"""Console-safe live bridge session facts.

The web/console import path needs pairing vocabulary and store management, but
must not load worker-only dependencies. Keep this module free of vendor SDKs and
QR rendering libraries; ``integrate.session`` owns the worker-only qrcode import.
"""

from __future__ import annotations

import enum
import shutil
import time
from pathlib import Path
from typing import Any

import strawberry
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from angee.integrate.locks import bridge_is_locked
from angee.tasks.locks import task_locks_are_cross_process

WAKE_SECONDS = 20.0
"""Upper bound between live-session desired-state / shutdown / lock checks."""

STOP_JOIN_SECONDS = 30.0
"""How long a stopping live session waits for the vendor connection to unwind."""

SESSION_EXIT_TIMEOUT = WAKE_SECONDS + STOP_JOIN_SECONDS + 20.0
"""Default destructive-reset wait: one wake, one vendor unwind, and headroom.

This bound derives from the real loop constants so a destructive reset cannot
silently drift below the session's maximum stop and unwind time.
"""


@strawberry.enum
class PairingState(enum.StrEnum):
    """The pairing vocabulary a live session reports and a connect dialog renders."""

    STARTING = "starting"
    AWAITING_SCAN = "awaiting_scan"
    PAIRED = "paired"
    PAUSED = "paused"
    LOGGED_OUT = "logged_out"
    STOPPED = "stopped"
    DUPLICATE_ACCOUNT = "duplicate_account"

    @classmethod
    def from_report(cls, value: object) -> PairingState | None:
        """Return the member one serialized report carries, or ``None``."""

        try:
            return cls(str(value or ""))
        except ValueError:
            return None


@strawberry.type(name="Pairing")
class PairingProjection:
    """Vendor-neutral pairing state projected by a live bridge implementation."""

    state: PairingState
    qr: str = ""
    own_id: str = ""
    account_label: str = ""
    duplicate_channel_id: str = ""
    duplicate_channel_name: str = ""


class SessionLoggedOut(Exception):
    """The linked account removed this live session and needs explicit reset."""


def session_store_path(bridge: Any) -> Path:
    """Return the bridge's session directory under the stack-persisted data dir."""

    return Path(settings.ANGEE_DATA_DIR) / bridge.live_impl.key / str(bridge.sqid)


def reset_session_store(bridge: Any) -> None:
    """Delete the bridge's session store after the caller proves it is released."""

    shutil.rmtree(session_store_path(bridge), ignore_errors=True)


def await_session_exit(bridge: Any, *, timeout: float | None = None) -> None:
    """Wait for the bridge's advisory lock to clear before touching its store."""

    if not task_locks_are_cross_process():
        raise ImproperlyConfigured(
            "Resetting live pairing needs a cross-process task lock backend to prove the "
            "live session released its store; this deployment's lock backend is process-local. "
            "Reset from a Postgres-backed deployment, or - if the retained session store is "
            "still usable - reconnect without wiping it by marking the integration disconnected "
            "and then connected, which clears the runtime error and starts a session."
        )
    deadline = time.monotonic() + (SESSION_EXIT_TIMEOUT if timeout is None else timeout)
    while bridge_is_locked(bridge):
        if time.monotonic() >= deadline:
            raise TimeoutError("The live session is still running; try again once it has stopped.")
        time.sleep(0.5)
