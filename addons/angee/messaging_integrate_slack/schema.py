"""GraphQL connect action for the optional Slack polling bridge."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_slack.connect import create_slack_channel


@strawberry.type
class MessagingSlackMutation:
    """Console action for connecting Slack workspace channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_slack_channel(
        self,
        info: strawberry.Info,
        name: str,
        token: str,
    ) -> ChannelType:
        """Verify a Slack user token and create its polling channel."""

        return cast(ChannelType, create_slack_channel(session_user(info), name=name, token=token))


schemas = {
    "console": {
        "mutation": [MessagingSlackMutation],
    },
}
