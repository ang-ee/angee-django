"""Settings fragments contributed by the storage-integrate addon."""

from __future__ import annotations

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_STORAGE_MOUNT_BACKEND_CLASSES": dict[str, str],
        "ANGEE_STORAGE_BACKEND_CLASSES.local_folder": str,
    },
)

SETTINGS: _Settings = {
    "ANGEE_STORAGE_MOUNT_BACKEND_CLASSES": {
        "local_folder": "angee.storage_integrate.mounts.LocalFolderMountBackend",
    },
    "ANGEE_STORAGE_BACKEND_CLASSES.local_folder": (
        "angee.storage_integrate.backends.LocalFolderBackend"
    ),
}
"""Django settings contributed when the storage-integrate addon is installed."""
