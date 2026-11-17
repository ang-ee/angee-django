"""Console GraphQL mutation for Google Drive Mounts."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.graphql.actions import session_user
from angee.graphql.ids import PublicID
from angee.iam.permissions import ADMIN_PERMISSION_CLASSES
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.schema import MountType, copy_target_backend, mount_credential
from angee.storage_integrate_gdrive.connect import create_google_drive_mount
from angee.storage_integrate_gdrive.mounts import GoogleDriveMountBackend


@strawberry.type
class StorageIntegrateGoogleDriveMutation:
    """Admin provisioning for Google Drive storage Mounts."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_google_drive(
        self,
        info: strawberry.Info,
        name: str,
        path: str,
        mode: MountMode,
        credential_id: PublicID | None = None,
        copy_backend_id: PublicID | None = None,
    ) -> MountType:
        """Connect a Google Drive folder with one of the user's Google accounts and queue its first sync.

        Provisioning requires the account (``Mount.objects.provision``), refusing its absence against ``credential``.
        """

        mount = create_google_drive_mount(
            session_user(info),
            name=name,
            credential=mount_credential(info, GoogleDriveMountBackend.key, credential_id),
            path=path,
            mode=mode,
            copy_backend=copy_target_backend(copy_backend_id),
        )
        return cast(MountType, mount)


schemas = {
    "console": {
        "mutation": [StorageIntegrateGoogleDriveMutation],
    },
}
"""GraphQL contributions installed by the Google Drive storage addon."""
