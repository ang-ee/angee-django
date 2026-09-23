"""GraphQL schema contributed by the optional IMAP messaging bridge."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Annotated, cast

import strawberry
import strawberry.experimental.pydantic
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.views.decorators.debug import sensitive_variables
from graphql import GraphQLError

from angee.graphql.actions import ActionResult, action_target, authorized_action_target
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_imap.backend import (
    ImapError,
    ImapSampleImport,
    ImapSampleMessage,
    ImapSamplePreview,
    ImapSamplePreviewRequest,
)
from angee.messaging_integrate_imap.connect import (
    ImapConnectError,
    connect_imap_channel,
    update_imap_channel_credential,
)

Channel = apps.get_model("messaging", "Channel")


@contextmanager
def _imap_errors() -> Iterator[None]:
    """Expose only addon-owned safe IMAP failures with one GraphQL error code."""

    try:
        yield
    except ImapError as error:
        raise GraphQLError(error.public_message, extensions={"code": "BAD_USER_INPUT"}) from error
    except ImapConnectError as error:
        raise GraphQLError(str(error), extensions={"code": "BAD_USER_INPUT"}) from error


@strawberry.experimental.pydantic.type(model=ImapSampleMessage, all_fields=True, name="ImapSampleMessage")
class ImapSampleMessageType:
    pass


@strawberry.experimental.pydantic.type(model=ImapSamplePreview, all_fields=True, name="ImapSamplePreview")
class ImapSamplePreviewType:
    pass


@strawberry.experimental.pydantic.type(model=ImapSampleImport, all_fields=True, name="ImapSampleImport")
class ImapSampleImportType:
    pass


@strawberry.type
class MessagingImapQuery:
    """Explicit read-only mailbox probes, admitted by the channel's write owner."""

    @strawberry.field(permission_classes=ADMIN_PERMISSION_CLASSES)
    def preview_imap_sample(
        self,
        info: strawberry.Info,
        id: PublicID,
        mailbox: str,
        since: date | None = None,
        before: date | None = None,
        all_dates: bool = False,
        uidvalidity: int | None = None,
        upper_uid: int | None = None,
        before_uid: int | None = None,
        total_count: int | None = None,
        limit: int = 20,
    ) -> ImapSamplePreviewType:
        """Preview one page from a frozen historical mailbox selection."""

        channel = authorized_action_target(info, Channel, id, "write")
        request = ImapSamplePreviewRequest(
            mailbox=mailbox,
            since=since,
            before=before,
            all_dates=all_dates,
            uidvalidity=uidvalidity,
            upper_uid=upper_uid,
            before_uid=before_uid,
            total_count=total_count,
            limit=limit,
        )
        with _imap_errors():
            return channel.preview_imap_sample(request, actor=session_user(info))


@strawberry.type
class MessagingImapMutation:
    """Console actions for connecting IMAP-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def prepare_imap_new_mail(self, info: strawberry.Info, id: PublicID) -> ActionResult:
        """Set a paused IMAP channel's cursor to the current mailbox boundary."""

        channel = authorized_action_target(info, Channel, id, "write")
        with _imap_errors():
            try:
                mailbox_count, changed = channel.prepare_imap_new_mail(actor=session_user(info))
            except ValidationError as error:
                return ActionResult(ok=False, message=" ".join(error.messages))
        if not changed:
            return ActionResult(
                ok=True,
                message=f"New-mail starting point already retained for {mailbox_count} mailbox(es).",
            )
        return ActionResult(
            ok=True,
            message=f"Set the new-mail starting point for {mailbox_count} mailbox(es). Resume to begin delivery.",
        )

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def import_imap_sample(
        self, info: strawberry.Info, id: PublicID, mailbox: str, uidvalidity: int, uids: list[int],
    ) -> ImapSampleImportType:
        """Import the explicit selection without activating live message triggers."""

        channel = authorized_action_target(info, Channel, id, "write")
        with _imap_errors():
            return channel.import_imap_sample(
                actor=session_user(info), mailbox=mailbox, uidvalidity=uidvalidity, uids=uids,
            )

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

        with _imap_errors():
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

        with (
            _imap_errors(),
            action_target(Channel, id, reason="messaging_integrate_imap.graphql.update_credential") as channel,
        ):
            update_imap_channel_credential(channel, username=username, password=password)
        return ActionResult(ok=True, message="Credential updated.")


schemas = {
    "console": {
        "query": [MessagingImapQuery],
        "mutation": [MessagingImapMutation],
    },
}
