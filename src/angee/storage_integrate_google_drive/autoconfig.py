"""Settings fragments contributed by the Google Drive Mount addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_STORAGE_MOUNT_BACKEND_CLASSES.google_drive": (
        "angee.storage_integrate_google_drive.mounts.GoogleDriveMountBackend"
    ),
    "ANGEE_STORAGE_BACKEND_CLASSES.google_drive": (
        "angee.storage_integrate_google_drive.backends.GoogleDriveStorageBackend"
    ),
}
"""Django settings contributed when the Google Drive Mount addon is installed."""
