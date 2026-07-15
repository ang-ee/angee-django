"""GraphQL schema contributed by the optional WhatsApp messaging bridge.

Thin console actions only: pairing state itself needs no dedicated surface —
the session mirrors it into ``sync_progress.details.pairing`` and the existing
``channelChanged`` subscription streams every update, so the connect dialog
just subscribes and renders the QR data URI.
"""

from __future__ import annotations

from typing import cast

import strawberry

from angee.graphql.actions import ActionResult, action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_whatsapp import connect

Channel = connect.Channel


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
        """Stop the live session, remove the linked device, disable the channel."""

        with action_target(
            Channel, id, reason="messaging_integrate_whatsapp.graphql.disconnect"
        ) as channel:
            connect.disconnect_whatsapp_channel(channel)
        return ActionResult(ok=True, message="Disconnected WhatsApp channel.")

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
        "mutation": [MessagingWhatsappMutation],
    },
}
