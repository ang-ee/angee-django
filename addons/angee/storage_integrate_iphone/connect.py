"""iPhone-backup Mount provisioning."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

from angee.integrate_iphone.backup import BackupError, IosBackup
from angee.storage_integrate.connect import provision_mount
from angee.storage_integrate.models import MountMode
from angee.storage_integrate.mounts import validate_local_folder_root
from angee.storage_integrate_iphone.mounts import IphoneBackupMountBackend


def create_iphone_backup_mount(
    user: Any,
    *,
    name: str,
    path: str,
    mode: MountMode | str,
) -> Any:
    """Validate and provision one unencrypted iPhone-backup Mount."""

    try:
        root = validate_local_folder_root(path)
        backup = IosBackup(root)
    except (BackupError, ValidationError) as error:
        if isinstance(error, ValidationError) and hasattr(error, "error_dict"):
            raise
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        raise ValidationError({"path": messages}) from error
    backup.close()
    return provision_mount(
        user,
        name=name,
        root=root,
        mode=mode,
        backend_class=IphoneBackupMountBackend.key,
        already_mounted_message="This iPhone backup is already mounted.",
        slug_default="iphone-backup",
        reason="storage_integrate_iphone.connect",
    )
