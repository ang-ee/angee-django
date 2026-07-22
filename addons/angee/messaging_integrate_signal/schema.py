"""GraphQL connect action for the optional Signal bridge."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_signal.connect import create_signal_channel


@strawberry.type
class MessagingSignalMutation:
    """Console action for linking Signal-backed message channels."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_signal_channel(self, info: strawberry.Info) -> ChannelType:
        """Create a Signal channel and start linked-device QR pairing."""

        return cast(ChannelType, create_signal_channel(session_user(info)))


schemas = {
    "console": {
        "mutation": [MessagingSignalMutation],
    },
}
