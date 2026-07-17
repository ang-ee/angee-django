"""GraphQL connect action for the optional Telegram bridge."""

from __future__ import annotations

from typing import cast

import strawberry
from django.apps import apps

from angee.graphql.actions import resolve_action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_telegram.connect import create_telegram_channel

Credential = apps.get_model("integrate", "Credential")


@strawberry.type
class MessagingTelegramMutation:
    """Console action for linking Telegram-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_telegram_channel(
        self,
        info: strawberry.Info,
        name: str,
        credential_id: PublicID,
    ) -> ChannelType:
        """Connect a channel to a selected app-key credential, then start QR pairing."""

        credential = resolve_action_target(
            Credential,
            credential_id,
            reason="messaging_integrate_telegram.graphql.credential.lookup",
        )
        channel = create_telegram_channel(
            session_user(info),
            name=name,
            credential=credential,
        )
        return cast(ChannelType, channel)


schemas = {
    "console": {
        "mutation": [MessagingTelegramMutation],
    },
}
