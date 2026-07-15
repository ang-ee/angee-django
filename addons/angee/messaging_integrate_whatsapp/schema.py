"""GraphQL schema contributed by the optional WhatsApp messaging bridge.

Thin console actions only: live pairing progress needs no dedicated surface —
the session mirrors it into ``sync_progress.details.pairing`` and the existing
``channelChanged`` subscription streams every update, so the connect dialog
just subscribes and renders the QR data URI. The one query here reconstructs
what a *reopened* dialog missed, and names a duplicate account's conflicting
channel from the caller's own scope. Both it and the mutations resolve a target
and dispatch to ``connect``; the vocabulary they exchange
(:class:`~.client.PairingState`, :class:`~.client.WhatsappPairingType`) belongs
to the addon, not to this module.
"""

from __future__ import annotations

from typing import cast

import strawberry

from angee.graphql.actions import ActionResult, action_target, resolve_action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_whatsapp import connect
from angee.messaging_integrate_whatsapp.client import WhatsappPairingType

Channel = connect.Channel


@strawberry.type
class MessagingWhatsappQuery:
    """WhatsApp-specific connection state for the console."""

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def whatsapp_pairing(self, id: PublicID) -> WhatsappPairingType:
        """Return pairing state reconstructed from durable identity and progress."""

        channel = resolve_action_target(
            Channel,
            id,
            reason="messaging_integrate_whatsapp.graphql.pairing",
        )
        return connect.whatsapp_pairing(channel)


@strawberry.type
class MessagingWhatsappMutation:
    """Console actions for linking WhatsApp-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_whatsapp_channel(self, info: strawberry.Info, name: str) -> ChannelType:
        """Create a WhatsApp channel and start QR pairing (watch channelChanged)."""

        channel = connect.connect_whatsapp_channel(session_user(info), name=name)
        return cast(ChannelType, channel)

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def disconnect_whatsapp_channel(self, id: PublicID) -> ActionResult:
        """Stop the live session while retaining reusable pairing material."""

        with action_target(
            Channel, id, reason="messaging_integrate_whatsapp.graphql.disconnect"
        ) as channel:
            connect.disconnect_whatsapp_channel(channel)
        return ActionResult(ok=True, message="Disconnected WhatsApp channel.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def resume_whatsapp_pairing(self, id: PublicID) -> ActionResult:
        """Resume a retained store or restart pairing without deleting it."""

        with action_target(
            Channel, id, reason="messaging_integrate_whatsapp.graphql.resume_pairing"
        ) as channel:
            connect.resume_whatsapp_pairing(channel)
        return ActionResult(ok=True, message="WhatsApp connection started.")

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def reset_whatsapp_pairing(self, id: PublicID) -> ActionResult:
        """Wipe the linked device and restart pairing with a fresh QR."""

        with action_target(
            Channel, id, reason="messaging_integrate_whatsapp.graphql.reset_pairing"
        ) as channel:
            connect.reset_whatsapp_pairing(channel)
        return ActionResult(ok=True, message="Pairing reset; scan the new QR code.")


schemas = {
    "console": {
        "query": [MessagingWhatsappQuery],
        "mutation": [MessagingWhatsappMutation],
    },
}
