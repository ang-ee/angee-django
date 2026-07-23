"""GraphQL connect action for the Discord Gateway bridge."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_discord.connect import create_discord_channel


@strawberry.type
class MessagingDiscordMutation:
    """Console action for connecting Discord bots."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_discord_channel(
        self,
        info: strawberry.Info,
        name: str,
        token: str,
    ) -> ChannelType:
        """Create a bot-token channel and start its Gateway session."""

        return cast(ChannelType, create_discord_channel(session_user(info), name, token))


schemas = {
    "console": {
        "mutation": [MessagingDiscordMutation],
    },
}
