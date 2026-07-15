"""WhatsApp channel connection service.

The optional WhatsApp addon owns the concrete bridge facts: the ``whatsapp``
backend key, the live-session lifecycle, and the session store that IS the
credential (whatsmeow's device pairing under ``ANGEE_DATA_DIR`` — no
``integrate.Credential`` row exists for a WhatsApp channel). Base ``messaging``
owns the neutral ``Channel`` model and the list/detail surface.

This module owns the operator's *intent*: every action here declares what the
operator asked for and dispatches: the lifecycle is that declared intent, so
asking to pair connects the row and the live desire follows it. Whether pairing
has actually succeeded is runtime state the session reports, never the
lifecycle. Connecting is two-phase: the channel row commits first, then
``start_live`` persists the live desire and enqueues the session task — the QR
code reaches the console through ``sync_progress`` on the ``channelChanged``
subscription. Disconnect persists the stopped/disconnected intent immediately
and lets the live session exit cooperatively while retaining its store. Pairing
reset is the destructive operation, so it waits (bounded) for the session's
advisory lock to clear before deleting that store — and refuses outright where
that lock cannot see another process's session at all.

These are the addon's action boundary, so they take an operator-named record and
must re-assert what the row's ``backend_class`` guarantees everywhere below
them: ``_require_whatsapp`` is that assertion, and it lives here rather than on
the backend the guarantee already selected.

Row writes specific to WhatsApp belong to the row's selected backend
(:class:`~.backend.WhatsAppChannelBackend`), not here.
"""

from __future__ import annotations

import time
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import system_context

from angee.integrate.locks import bridge_is_locked
from angee.integrate.models import IntegrationLifecycle, IntegrationRuntimeStatus
from angee.messaging_integrate_whatsapp.backend import WhatsAppChannelBackend
from angee.messaging_integrate_whatsapp.client import (
    STOP_JOIN_SECONDS,
    WAKE_SECONDS,
    WhatsappPairingType,
    reset_session_store,
)
from angee.tasks.locks import task_locks_are_cross_process

Channel = apps.get_model("messaging", "Channel")
Vendor = apps.get_model("integrate", "Vendor")

_WHATSAPP_SLUG = WhatsAppChannelBackend.key
"""This addon's one name — the seeded vendor catalogue slug and the channel
backend registry key are the same fact spelled once."""

SESSION_EXIT_TIMEOUT = WAKE_SECONDS + STOP_JOIN_SECONDS + 20.0
"""How long pairing reset waits for a stopping session to release its lock.

Bounded above the worst-case cooperative stop: one wake to notice the persisted
desire (``WAKE_SECONDS``) plus the vendor connection's unwind
(``STOP_JOIN_SECONDS`` — a WhatsApp WebSocket close can be slow), with headroom.
Below this the wait could reject a session that is stopping cleanly."""


def create_whatsapp_channel(user: Any, *, name: str) -> Any:
    """Create a disconnected WhatsApp channel without starting a live session.

    The import-only shape: ``whatsapp_import`` targets it, and pairing can
    start later through :func:`connect_whatsapp_channel` / ``start_live``.
    """

    display_name = str(name).strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    with system_context(reason="messaging_integrate_whatsapp.create"), transaction.atomic():
        channel = Channel.objects.create(
            vendor=_whatsapp_vendor(),
            owner=user,
            backend_class=_WHATSAPP_SLUG,
            display_name=display_name,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            created_by_id=user.pk,
        )
    return channel


def connect_whatsapp_channel(user: Any, *, name: str) -> Any:
    """Create a WhatsApp channel, declare it connected, and start its pairing session.

    The row commits disconnected so a failed resume leaves no half-connected
    channel behind; :func:`resume_whatsapp_pairing` then declares the intent.
    The session task lands on the dedicated ``whatsapp`` queue; without that
    worker (the stack input not enabled) the start simply expires and the
    channel keeps reporting the ``starting`` pairing state until the
    reconciler's next start reaches a live worker.
    """

    channel = create_whatsapp_channel(user, name=name)
    resume_whatsapp_pairing(channel)
    return channel


def resume_whatsapp_pairing(channel: Any) -> None:
    """Declare this channel connected, clear its runtime error, and resume pairing.

    The one path that turns "the operator wants this channel connected" into
    lifecycle intent, from a paused channel, a disconnected one with a retained
    device store, or a never-paired one. The identity claim deliberately owns no
    part of this: a live session proves *which account* is linked, it does not
    get to decide that a connection was wanted.

    The runtime error is cleared here, at the verb, rather than as a side effect
    of the lifecycle edge: ``set_lifecycle`` returns early when the row already
    reads CONNECTED, so a CONNECTED+ERROR row — a logout or a rejected duplicate
    the operator is retrying — would keep the ERROR that makes ``ensure_sessions``
    skip it, and this repair declaration would buy exactly one dispatch. Clearing
    it is the operator saying "try this again"; ``report_status`` owns exactly
    those fields and is idempotent on an already-healthy row.
    """

    _require_whatsapp(channel)
    with system_context(reason="messaging_integrate_whatsapp.resume"):
        channel.refresh_from_db()
        channel.set_lifecycle(IntegrationLifecycle.CONNECTED)
        channel.report_status(IntegrationRuntimeStatus.OK)
        channel.start_live()


def disconnect_whatsapp_channel(channel: Any) -> None:
    """Stop and release the account immediately while retaining its device store."""

    _require_whatsapp(channel)
    with system_context(reason="messaging_integrate_whatsapp.disconnect"):
        channel.stop_live()
        channel.backend.mark_disconnected(clear_identity=False)


def reset_whatsapp_pairing(channel: Any) -> None:
    """Wipe the linked device and restart pairing — the way back from logged-out."""

    _require_whatsapp(channel)
    with system_context(reason="messaging_integrate_whatsapp.reset_pairing"):
        channel.stop_live()
        _await_session_exit(channel)
        reset_session_store(channel)
        channel.backend.mark_disconnected(clear_identity=True)
    resume_whatsapp_pairing(channel)


def whatsapp_pairing(channel: Any) -> WhatsappPairingType:
    """Return the console's pairing projection for one WhatsApp channel.

    The projection itself belongs to the row's backend, which owns both the
    durable identity and the transient report it merges; this is the addon's
    action boundary, symmetric with the mutations above.
    """

    _require_whatsapp(channel)
    return channel.backend.pairing()


def _await_session_exit(channel: Any, *, timeout: float | None = None) -> None:
    """Wait for the session's advisory lock to clear before touching its store.

    An unlinked-but-open SQLite store keeps being written — the wipe would
    silently not happen — so a session that outlives the bounded wait fails the
    operation loudly instead.

    Refuses outright on a process-local lock backend. ``bridge_is_locked`` can
    only see another process's session when the lock backend is cross-process
    (``angee.tasks.locks.LockBackend.cross_process``); on the SQLite/dev floor it
    answers ``False`` for a session a worker is holding right now. Deriving "no
    session is running" from a probe that cannot see one would make this wait
    return immediately and hand a live store to ``rmtree``, so the destructive
    reset is declined rather than run on an unproven store.
    """

    if not task_locks_are_cross_process():
        raise ImproperlyConfigured(
            "Resetting WhatsApp pairing needs a cross-process task lock backend to prove the "
            "live session released its store; this deployment's lock backend is process-local "
            "(the SQLite/dev floor). Reset from a Postgres-backed deployment, or — if the "
            "retained device store is still usable — re-pair without wiping it by "
            "disconnecting and reconnecting the channel (markIntegrationDisconnected then "
            "markIntegrationConnected), which clears the runtime error and starts a session."
        )
    deadline = time.monotonic() + (SESSION_EXIT_TIMEOUT if timeout is None else timeout)
    while bridge_is_locked(channel):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "The live WhatsApp session is still running; try again once it has stopped."
            )
        time.sleep(0.5)


def _require_whatsapp(channel: Any) -> None:
    """Reject records outside this addon's backend boundary."""

    if str(channel.backend_class) != _WHATSAPP_SLUG:
        raise ValueError("This action requires a WhatsApp channel.")


def _whatsapp_vendor() -> Any:
    """Return the addon-seeded WhatsApp vendor row, failing clearly on drift."""

    try:
        return Vendor.objects.get(slug=_WHATSAPP_SLUG)
    except Vendor.DoesNotExist as exc:
        raise ImproperlyConfigured(
            "WhatsApp vendor is missing. Load messaging_integrate_whatsapp resources "
            "before connecting WhatsApp channels."
        ) from exc
