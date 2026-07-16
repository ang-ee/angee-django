"""GraphQL connect action for the optional Telegram bridge."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_telegram.connect import create_telegram_channel


@strawberry.type
class MessagingTelegramMutation:
    """Console action for linking Telegram-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_telegram_channel(
        self,
        info: strawberry.Info,
        name: str,
        api_id: str,
        api_hash: str,
    ) -> ChannelType:
        """Create an app-key credential and connected channel, then start QR pairing."""

        channel = create_telegram_channel(
            session_user(info),
            name=name,
            api_id=api_id,
            api_hash=api_hash,
        )
        return cast(ChannelType, channel)


schemas = {
    "console": {
        "mutation": [MessagingTelegramMutation],
    },
}
