"""HubSpot's connect mutation contribution."""

from typing import cast

import strawberry
from django.apps import apps

from angee.graphql.actions import session_user
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.integrate.credentials import CredentialKind
from angee.parties.schema import DirectoryType
from angee.parties_integrate_hubspot.backends import HubSpotDirectoryBackend


@strawberry.type
class HubSpotMutation:
    """Connect the account this addon reads."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_hubspot_directory(self, info: strawberry.Info, name: str, token: str) -> DirectoryType:
        """Connect the HubSpot account a service key reads, its connection tested before anything is saved."""

        return cast(DirectoryType, apps.get_model("parties", "Directory").objects.connect(
            session_user(info),
            backend_class=HubSpotDirectoryBackend.key,
            display_name=name,
            credential_kind=CredentialKind.STATIC_TOKEN,
            material={"api_key": token},
        ))


schemas = {name: {"mutation": [HubSpotMutation]} for name in ("public", "console")}
