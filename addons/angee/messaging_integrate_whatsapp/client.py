"""Neonize-free session facts — safe to import from the web/console process.

The live session itself (the ``neonize``/whatsmeow binding) lives in
:mod:`.session`, which only the ``whatsapp`` queue worker imports. The constants,
the pairing vocabulary, the logged-out signal, and the session-store paths below
carry no vendor dependency, so ``connect.py`` (and transitively the console
schema) import them at module top without ever loading the Go library.

The pairing vocabulary crosses GraphQL as the real :class:`PairingState` enum,
projected through :class:`WhatsappPairingType`, so the emitted SDL owns the
frontend mirror through codegen — there is no hand-maintained copy to keep in
step. Both types live here beside the vocabulary rather than in ``schema.py``
because :class:`~.backend.WhatsAppChannelBackend` — the row owner that builds
the projection — must not import the console schema.

Every WhatsApp-specific write to a channel row lives on that backend, selected
per row by ``backend_class``; this module holds no ORM access at all. It reads a
channel only to name that channel's own directory (``session_store_path`` takes
the ``sqid``) — a path fact, not a query.
"""

from __future__ import annotations

import enum
import shutil
from pathlib import Path
from typing import Any

import strawberry
from django.conf import settings

from angee.messaging_integrate_whatsapp.parser import bare_jid
from angee.tasks.locks import LockKey

WAKE_SECONDS = 20.0
"""Upper bound between a live session's desired-state / shutdown / lock checks —
shorter than the reconciler tick (:data:`.constants.RECONCILER_INTERVAL`) so a
lost lock exits before a duplicate session starts."""

STOP_JOIN_SECONDS = 30.0
"""How long a stopping session waits for the Go connection to unwind."""


@strawberry.enum
class PairingState(enum.StrEnum):
    """The pairing vocabulary a live session reports and the connect dialog renders.

    A session serializes its own progress into ``sync_progress.details.pairing``
    (the member value is that token; the upper-case member name is the wire enum
    value). :meth:`~.backend.WhatsAppChannelBackend.pairing` answers ``PAUSED``,
    ``PAIRED`` and ``STOPPED`` from the durable row instead, so only
    ``STARTING``, ``AWAITING_SCAN``, ``LOGGED_OUT`` and ``DUPLICATE_ACCOUNT``
    are ever read back out of a report.
    """

    STARTING = "starting"
    AWAITING_SCAN = "awaiting_scan"
    PAIRED = "paired"
    PAUSED = "paused"
    LOGGED_OUT = "logged_out"
    STOPPED = "stopped"
    DUPLICATE_ACCOUNT = "duplicate_account"

    @classmethod
    def from_report(cls, value: object) -> PairingState | None:
        """Return the member one serialized report carries, or ``None`` for anything else."""

        try:
            return cls(str(value or ""))
        except ValueError:
            return None


@strawberry.type
class WhatsappPairingType:
    """Durable/transient pairing projection for a reopenable connection dialog."""

    state: PairingState
    qr: str = ""
    jid: str = ""
    phone: str = ""
    duplicate_channel_id: str = ""
    duplicate_channel_name: str = ""


class SessionLoggedOut(Exception):
    """The linked phone unlinked this device — pairing must be explicitly reset."""


class DuplicateAccountRejected(Exception):
    """The scanned account is already claimed by another channel.

    Carries no control flow — the session reports ``DUPLICATE_ACCOUNT`` as its
    final state rather than raising. It exists so the rejection reaches
    ``record_sync_error``, the one owner of runtime-failure telemetry, in the
    shape that owner takes.
    """


def whatsapp_account_lock_key(jid: str) -> LockKey:
    """Return the cross-worker ownership key for one normalized WhatsApp account."""

    normalized = bare_jid(jid)
    if not normalized:
        raise ValueError("A WhatsApp account JID is required.")
    return LockKey("whatsapp-account", (normalized,))


def session_store_path(channel: Any) -> Path:
    """Return the channel's session directory under the stack-persisted data dir."""

    return Path(settings.ANGEE_DATA_DIR) / "whatsapp" / str(channel.sqid)


def reset_session_store(channel: Any) -> None:
    """Delete the channel's session store; the caller must first prove nothing holds it.

    An unlinked-but-open SQLite store keeps being written, so this wipe silently
    does not happen against a store the vendor client still holds — and
    ``ignore_errors`` swallows the half-deleted tree it leaves behind. Two
    callers own that proof: ``connect.reset_whatsapp_pairing`` through its
    bounded wait on the session's advisory lock
    (``connect._await_session_exit``), and
    :meth:`~.session.WhatsAppSession.discard_store` through the session's own
    confirmed vendor-thread exit.
    """

    shutil.rmtree(session_store_path(channel), ignore_errors=True)
