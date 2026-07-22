"""GraphQL connect action for the Matrix bridge."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.messaging.schema import ChannelType
from angee.messaging_integrate_matrix.connect import create_matrix_channel


@strawberry.type
class MessagingMatrixMutation:
    """Console action for linking password-authenticated Matrix accounts."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_matrix_channel(
        self,
        info: strawberry.Info,
        homeserver: str,
        username: str,
        password: str,
    ) -> ChannelType:
        """Connect a Matrix account, then request its optional recovery key."""

        return cast(
            ChannelType,
            create_matrix_channel(session_user(info), homeserver, username, password),
        )


schemas = {
    "console": {
        "mutation": [MessagingMatrixMutation],
    },
}
