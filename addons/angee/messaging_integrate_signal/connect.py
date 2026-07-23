"""Signal channel creation over a linked-device session store credential."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.db import transaction
from rebac import system_context

from angee.messaging.connect import resume_channel_pairing
from angee.messaging_integrate_signal.backend import SignalChannelBackend

Channel = apps.get_model("messaging", "Channel")

_SIGNAL_SLUG = SignalChannelBackend.key


def create_signal_channel(user: Any) -> Any:
    """Create a Signal channel and start linked-device QR pairing.

    Signal has no separate ``integrate.Credential``: signal-cli's per-channel
    config directory is the credential and is retained until explicit reset.
    """

    with system_context(reason="messaging_integrate_signal.create"), transaction.atomic():
        channel = Channel.objects.create_disconnected(
            user,
            name=SignalChannelBackend.label,
            backend_class=_SIGNAL_SLUG,
        )
    resume_channel_pairing(channel)
    return channel
