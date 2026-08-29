"""GraphQL schema contributed by the optional IMAP messaging bridge."""

from __future__ import annotations

from typing import Annotated, cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_imap.connect import connect_imap_channel


@strawberry.type
class MessagingImapMutation:
    """Console actions for connecting IMAP-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_imap_channel(
        self,
        info: strawberry.Info,
        name: str,
        host: str,
        username: str,
        password: str,
        security: str = "ssl",
        port: int | None = None,
        mailboxes: list[str] | None = None,
        own_addresses: Annotated[
            list[str] | None,
            strawberry.argument(name="own_addresses"),
        ] = None,
    ) -> ChannelType:
        """Create a Basic-auth credential and active IMAP channel for sync."""

        channel = connect_imap_channel(
            session_user(info),
            name=name,
            host=host,
            username=username,
            password=password,
            security=security,
            port=port,
            mailboxes=mailboxes,
            own_addresses=own_addresses,
        )
        return cast(ChannelType, channel)


schemas = {
    "console": {
        "mutation": [MessagingImapMutation],
    },
}
