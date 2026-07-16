"""WhatsApp channel connection service.

The optional WhatsApp addon owns the concrete bridge facts: the ``whatsapp``
backend key, the live-session lifecycle, and the session store that IS the
credential (whatsmeow's device pairing under ``ANGEE_DATA_DIR`` — no
``integrate.Credential`` row exists for a WhatsApp channel). Base ``messaging``
owns the neutral ``Channel`` model and the list/detail surface.

Connecting is two-phase: the channel row commits first, then messaging's generic
pairing service declares the live intent and dispatches the session task. This
addon retains the vendor-named create/connect facade; generic pairing lifecycle
verbs stay with messaging.

Row writes specific to WhatsApp belong to the row's selected backend
(:class:`~.backend.WhatsAppChannelBackend`), not here.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_whatsapp.backend import WhatsAppChannelBackend

Channel = apps.get_model("messaging", "Channel")
Vendor = apps.get_model("integrate", "Vendor")

_WHATSAPP_SLUG = WhatsAppChannelBackend.key
"""This addon's one name — the seeded vendor catalogue slug and the channel
backend registry key are the same fact spelled once."""


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
    channel behind; :func:`angee.messaging.connect.resume_channel_pairing` then
    declares the intent.
    The session task lands on the dedicated ``whatsapp`` queue; without that
    worker (the stack input not enabled) the start simply expires and the
    channel keeps reporting the ``starting`` pairing state until the
    reconciler's next start reaches a live worker.
    """

    channel = create_whatsapp_channel(user, name=name)
    resume_channel_pairing(channel)
    return channel


def _whatsapp_vendor() -> Any:
    """Return the addon-seeded WhatsApp vendor row, failing clearly on drift."""

    try:
        return Vendor.objects.get(slug=_WHATSAPP_SLUG)
    except Vendor.DoesNotExist as exc:
        raise ImproperlyConfigured(
            "WhatsApp vendor is missing. Load messaging_integrate_whatsapp resources "
            "before connecting WhatsApp channels."
        ) from exc
