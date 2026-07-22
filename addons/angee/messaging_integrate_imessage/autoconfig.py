"""Settings fragments contributed by the iMessage channel backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the iMessage backend into the channel backend registry. Dotted
    # key so it merges into messaging's default rather than replacing it.
    "ANGEE_CHANNEL_BACKEND_CLASSES.imessage": "angee.messaging_integrate_imessage.backend.ImessageChannelBackend",
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.imessage_iphone_backup": (
        "angee.messaging_integrate_imessage.extractor.ImessageIphoneBackupExtractor"
    ),
    # Mounted iPhone-backup drives: one extractor (SMS + iMessage share one store).
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.imessage_iphone_mount": (
        "angee.messaging_integrate_imessage.mount_extractor.ImessageMountBackupExtractor"
    ),
}
"""Django settings contributed when the iMessage channel addon is installed."""
