"""GraphQL schema contributed by the optional IMAP messaging bridge."""

from __future__ import annotations

from typing import Annotated, cast

import strawberry
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.views.decorators.debug import sensitive_variables
from graphql import GraphQLError

from angee.graphql.actions import ActionResult, action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_imap.connect import (
    ImapConnectError,
    connect_imap_channel,
    update_imap_channel_credential,
)

Channel = apps.get_model("messaging", "Channel")


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

        try:
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
        except ImapConnectError as error:
            raise GraphQLError(str(error), extensions={"code": "BAD_USER_INPUT"}) from error
        except ImproperlyConfigured as error:
            raise GraphQLError(
                "IMAP integration is not configured.",
                extensions={"code": "BAD_USER_INPUT"},
            ) from error
        return cast(ChannelType, channel)

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    @sensitive_variables("password")
    def update_imap_channel_credential(self, id: PublicID, username: str, password: str) -> ActionResult:
        """Re-enter an IMAP channel's username/password without recreating the channel.

        The record verb behind the channel page's "Update credential" form: the
        channel and its credential stay, only the encrypted material changes.
        Follow it with "Test connection" to prove the new login.
        """

        with action_target(Channel, id, reason="messaging_integrate_imap.graphql.update_credential") as channel:
            try:
                update_imap_channel_credential(channel, username=username, password=password)
            except ImapConnectError as error:
                return ActionResult(ok=False, message=str(error))
        return ActionResult(ok=True, message="Credential updated.")


schemas = {
    "console": {
        "mutation": [MessagingImapMutation],
    },
}
