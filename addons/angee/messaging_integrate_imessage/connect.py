"""iMessage channel creation service.

iMessage is import-only, so this addon owns just the disconnected-channel create
that ``imessage_import`` targets — there is no live pairing lifecycle (contrast
:mod:`angee.messaging_integrate_whatsapp.connect`, which also starts a session).
Base ``messaging`` owns the neutral ``Channel`` model and its list/detail surface.
"""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.messaging_integrate_imessage.backend import ImessageChannelBackend

_IMESSAGE_SLUG = ImessageChannelBackend.key
"""This addon's one name — the seeded vendor catalogue slug and the channel
backend registry key are the same fact spelled once."""


def create_imessage_channel(user: Any, *, name: str) -> Any:
    """Create a disconnected iMessage channel for a backup import to land on.

    The ``Channel`` / ``Vendor`` models are resolved lazily (as in :mod:`.backend`)
    so importing this module never touches the app registry — the per-line importer
    imports it at module load, before the concrete channel model is registered.
    """

    display_name = str(name).strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_imessage.create"), transaction.atomic():
        channel = channel_model.objects.create_disconnected(
            user,
            name=display_name,
            backend_class=_IMESSAGE_SLUG,
        )
    return channel


def get_or_create_imessage_channel(user: Any, *, name: str) -> Any:
    """Return ``user``'s iMessage channel named ``name``, creating it once.

    The per-line import creates one channel per local line and re-runs to resume,
    so channel creation must be idempotent: an existing disconnected channel for
    this owner with the same ``(backend_class, display_name)`` is reused rather
    than duplicated, and :func:`create_imessage_channel` mints it on first sight.
    """

    display_name = str(name).strip()
    if not display_name:
        raise ValueError("A channel name is required.")
    channel_model = apps.get_model("messaging", "Channel")
    with system_context(reason="messaging_integrate_imessage.get_or_create"):
        existing = channel_model._base_manager.filter(
            owner=user, backend_class=_IMESSAGE_SLUG, display_name=display_name
        ).first()
    if existing is not None:
        return existing
    return create_imessage_channel(user, name=display_name)
