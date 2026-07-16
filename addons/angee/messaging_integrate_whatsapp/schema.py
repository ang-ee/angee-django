"""GraphQL connect action for the optional WhatsApp bridge."""

from __future__ import annotations

from typing import cast

import strawberry

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


schemas = {
    "console": {
        "mutation": [MessagingWhatsappMutation],
    },
}
