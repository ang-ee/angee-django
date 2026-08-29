"""Settings fragments contributed by the iPhone-backup Mount addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_STORAGE_MOUNT_BACKEND_CLASSES.iphone_backup": (
        "angee.storage_integrate_iphone.mounts.IphoneBackupMountBackend"
    ),
    "ANGEE_STORAGE_BACKEND_CLASSES.iphone_backup": (
        "angee.storage_integrate_iphone.backends.IphoneBackupStorageBackend"
    ),
}
"""Django settings contributed when the iPhone-backup Mount addon is installed."""
