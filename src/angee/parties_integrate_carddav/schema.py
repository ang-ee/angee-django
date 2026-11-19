"""CardDAV's optional connect mutation contribution."""

from typing import cast

import strawberry
from django.apps import apps

from angee.graphql.actions import session_user
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.integrate.credentials import CredentialKind
from angee.parties.schema import DirectoryType
from angee.parties_integrate_carddav.backend import CardDavDirectoryBackend


@strawberry.type
class CardDavMutation:
    """Connect the protocol owned by this addon."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_card_dav_directory(
        self, info: strawberry.Info, name: str, server_url: str, username: str, password: str,
    ) -> DirectoryType:
        """Connect a Basic-auth CardDAV account, its connection tested before anything is saved."""

        return cast(DirectoryType, apps.get_model("parties", "Directory").objects.connect(
            session_user(info),
            backend_class=CardDavDirectoryBackend.key,
            display_name=name,
            credential_kind=CredentialKind.BASIC_AUTH,
            material={"username": username, "password": password},
            config={"server_url": server_url},
        ))


schemas = {name: {"mutation": [CardDavMutation]} for name in ("public", "console")}
