"""Console GraphQL mutation for iPhone-backup Mounts."""

from __future__ import annotations

from typing import cast

import strawberry

from angee.iam.permissions import ADMIN_PERMISSION_CLASSES, session_user
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.schema import MountType
from angee.storage_integrate_iphone.connect import create_iphone_backup_mount


@strawberry.type
class StorageIntegrateIphoneMutation:
    """Admin provisioning for iPhone-backup storage Mounts."""

    @strawberry.mutation(permission_classes=ADMIN_PERMISSION_CLASSES)
    def connect_iphone_backup(
        self,
        info: strawberry.Info,
        name: str,
        path: str,
        mode: MountMode,
    ) -> MountType:
        """Connect an unencrypted Finder/iTunes backup and queue its first sync."""

        mount = create_iphone_backup_mount(
            session_user(info),
            name=name,
            path=path,
            mode=mode,
        )
        return cast(MountType, mount)


schemas = {
    "console": {
        "mutation": [StorageIntegrateIphoneMutation],
    },
}
"""GraphQL contributions installed by the iPhone-backup storage addon."""
