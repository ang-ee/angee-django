"""Signal channel creation over a linked-device session store credential."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.integrate.models import IntegrationLifecycle
from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_signal.backend import SignalChannelBackend

Channel = apps.get_model("messaging", "Channel")
Vendor = apps.get_model("integrate", "Vendor")

_SIGNAL_SLUG = SignalChannelBackend.key


def create_signal_channel(user: Any) -> Any:
    """Create a Signal channel and start linked-device QR pairing.

    Signal has no separate ``integrate.Credential``: signal-cli's per-channel
    config directory is the credential and is retained until explicit reset.
    """

    with system_context(reason="messaging_integrate_signal.create"), transaction.atomic():
        channel = Channel.objects.create(
            vendor=Vendor.objects.seeded(_SIGNAL_SLUG),
            owner=user,
            backend_class=_SIGNAL_SLUG,
            display_name=SignalChannelBackend.label,
            lifecycle=IntegrationLifecycle.DISCONNECTED,
            created_by_id=user.pk,
        )
    resume_channel_pairing(channel)
    return channel
