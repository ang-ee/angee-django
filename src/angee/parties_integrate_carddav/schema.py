"""CardDAV's optional connect mutation contribution."""

from typing import cast

import strawberry

from angee.graphql.actions import session_user
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.parties.schema import DirectoryType
from angee.parties_integrate_carddav.connect import connect_directory


@strawberry.type
class CardDavMutation:
    """Connect the protocol owned by this addon."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_card_dav_directory(
        self, info: strawberry.Info, name: str, server_url: str, username: str, password: str,
    ) -> DirectoryType:
        """Probe and retain a Basic-auth directory through its connection owner."""

        return cast(DirectoryType, connect_directory(
            session_user(info), name=name, server_url=server_url, username=username, password=password,
        ))


schemas = {name: {"mutation": [CardDavMutation]} for name in ("public", "console")}
