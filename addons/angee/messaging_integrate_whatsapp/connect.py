"""WhatsApp channel connection service.

The optional WhatsApp addon owns the concrete bridge facts: the ``whatsapp``
backend key, the live-session lifecycle, and the session store that IS the
credential (whatsmeow's device pairing under ``ANGEE_DATA_DIR`` — no
``integrate.Credential`` row exists for a WhatsApp channel). Base ``messaging``
owns the neutral ``Channel`` model and the list/detail surface.

Connecting is two-phase: the channel row commits first, then ``start_live``
persists the live desire and enqueues the session task — the QR code reaches
the console through ``sync_progress`` on the ``channelChanged`` subscription.
Disconnect and pairing reset must not unlink a store an active session still
has open, so both wait (bounded) for the session's advisory lock to clear
after ``stop_live`` before touching files.
"""

from __future__ import annotations

import time
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import system_context

from angee.integrate.locks import bridge_is_locked
from angee.messaging_integrate_whatsapp.client import (
    STOP_JOIN_SECONDS,
    WAKE_SECONDS,
    reset_session_store,
)

Channel = apps.get_model("messaging", "Channel")
Vendor = apps.get_model("integrate", "Vendor")

_WHATSAPP_VENDOR_SLUG = "whatsapp"

SESSION_EXIT_TIMEOUT = WAKE_SECONDS + STOP_JOIN_SECONDS + 20.0
"""How long disconnect/reset wait for a stopping session to release its lock.

Bounded above the worst-case cooperative stop: one wake to notice the persisted
desire (``WAKE_SECONDS``) plus the vendor connection's unwind
(``STOP_JOIN_SECONDS`` — a WhatsApp WebSocket close can be slow), with headroom.
Below this the wait could reject a session that is stopping cleanly."""


def create_whatsapp_channel(user: Any, *, name: str) -> Any:
    """Create a draft WhatsApp channel without starting a live session.

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
            backend_class=_WHATSAPP_VENDOR_SLUG,
            display_name=display_name,
            lifecycle="draft",
            created_by_id=user.pk,
        )
        channel.activate()
    return channel


def connect_whatsapp_channel(user: Any, *, name: str) -> Any:
    """Create an active WhatsApp channel and start its pairing session.

    The session task lands on the dedicated ``whatsapp`` queue; without that
    worker (the stack input not enabled) the start simply expires and the
    channel keeps reporting the ``starting`` pairing state until the
    reconciler's next start reaches a live worker.
    """

    channel = create_whatsapp_channel(user, name=name)
    with system_context(reason="messaging_integrate_whatsapp.connect"):
        channel.start_live()
    return channel


def disconnect_whatsapp_channel(channel: Any) -> None:
    """Stop the live session, remove the linked-device store, disable the channel."""

    with system_context(reason="messaging_integrate_whatsapp.disconnect"):
        channel.stop_live()
        _await_session_exit(channel)
        reset_session_store(channel)
        channel.disable()


def reset_whatsapp_pairing(channel: Any) -> None:
    """Wipe the linked device and restart pairing — the way back from logged-out."""

    with system_context(reason="messaging_integrate_whatsapp.reset_pairing"):
        channel.stop_live()
        _await_session_exit(channel)
        reset_session_store(channel)
        channel.start_live()


def _await_session_exit(channel: Any, *, timeout: float | None = None) -> None:
    """Wait for the session's advisory lock to clear before touching its store.

    An unlinked-but-open SQLite store keeps being written — the wipe would
    silently not happen — so a session that outlives the bounded wait fails the
    operation loudly instead.
    """

    deadline = time.monotonic() + (SESSION_EXIT_TIMEOUT if timeout is None else timeout)
    while bridge_is_locked(channel):
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "The live WhatsApp session is still running; try again once it has stopped."
            )
        time.sleep(0.5)


def _whatsapp_vendor() -> Any:
    """Return the addon-seeded WhatsApp vendor row, failing clearly on drift."""

    try:
        return Vendor.objects.get(slug=_WHATSAPP_VENDOR_SLUG)
    except Vendor.DoesNotExist as exc:
        raise ImproperlyConfigured(
            "WhatsApp vendor is missing. Load messaging_integrate_whatsapp resources "
            "before connecting WhatsApp channels."
        ) from exc
