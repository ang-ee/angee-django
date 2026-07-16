"""WhatsApp console pairing projection.

Generic live-session facts live in :mod:`angee.integrate.live` so the console can
import them without loading the WhatsApp worker binding. This module keeps only
the WhatsApp-specific GraphQL projection.
"""

from __future__ import annotations

import strawberry

from angee.integrate.live import PairingState


@strawberry.type
class WhatsappPairingType:
    """Durable/transient pairing projection for a reopenable WhatsApp dialog."""

    state: PairingState
    qr: str = ""
    jid: str = ""
    phone: str = ""
    duplicate_channel_id: str = ""
    duplicate_channel_name: str = ""


class DuplicateAccountRejected(Exception):
    """The scanned WhatsApp account is already claimed by another channel."""
