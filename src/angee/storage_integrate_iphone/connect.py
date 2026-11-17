"""iPhone-backup Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.apps import apps

from angee.storage_integrate.models import MountMode
from angee.storage_integrate_iphone.mounts import IphoneBackupMountBackend


def create_iphone_backup_mount(
    user: Any,
    *,
    name: str,
    path: str,
    mode: MountMode,
) -> Any:
    """Provision one unencrypted iPhone-backup Mount; the backend canonicalizes and opens the backup."""

    return apps.get_model("storage_integrate", "Mount").objects.provision(
        user,
        name=name,
        root=path,
        mode=mode,
        backend_class=IphoneBackupMountBackend.key,
        already_mounted_message="This iPhone backup is already mounted.",
        slug_default="iphone-backup",
        reason="storage_integrate_iphone.connect",
    )
