"""Neonize-free session facts — safe to import from the web/console process.

The live session itself (the ``neonize``/whatsmeow binding) lives in
:mod:`.session`, which only the ``whatsapp`` queue worker imports. The constants,
the pairing vocabulary, the logged-out signal, and the session-store paths below
carry no vendor dependency, so ``connect.py`` (and transitively the console
schema) import them at module top without ever loading the Go library.

The :class:`PairingState` vocabulary is mirrored on the frontend as the
``WhatsappPairing`` union in ``web/src/documents.ts``; keep the two in step.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from django.conf import settings

WAKE_SECONDS = 20.0
"""Upper bound between a live session's desired-state / shutdown / lock checks —
shorter than the reconciler tick (:data:`.constants.RECONCILER_INTERVAL`) so a
lost lock exits before a duplicate session starts."""

STOP_JOIN_SECONDS = 30.0
"""How long a stopping session waits for the Go connection to unwind."""


class PairingState:
    """The pairing vocabulary serialized into ``sync_progress.details.pairing``."""

    STARTING = "starting"
    AWAITING_SCAN = "awaiting_scan"
    PAIRED = "paired"
    LOGGED_OUT = "logged_out"
    STOPPED = "stopped"


class SessionLoggedOut(Exception):
    """The linked phone unlinked this device — pairing must be explicitly reset."""


def session_store_path(channel: Any) -> Path:
    """Return the channel's session directory under the stack-persisted data dir."""

    return Path(settings.ANGEE_DATA_DIR) / "whatsapp" / str(channel.sqid)


def reset_session_store(channel: Any) -> None:
    """Delete the channel's session store — the explicit pairing reset only."""

    shutil.rmtree(session_store_path(channel), ignore_errors=True)
