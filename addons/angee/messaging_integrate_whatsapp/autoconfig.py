"""Settings fragments contributed by the WhatsApp channel backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the WhatsApp backend into the channel backend registry. Dotted
    # key so it merges into messaging's default rather than replacing it.
    "ANGEE_CHANNEL_BACKEND_CLASSES.whatsapp": "angee.messaging_integrate_whatsapp.backend.WhatsAppChannelBackend",
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.whatsapp_iphone_backup": (
        "angee.messaging_integrate_whatsapp.extractor.WhatsAppIphoneBackupExtractor"
    ),
    # Mounted iPhone-backup drives: one extractor per WhatsApp app domain so a
    # two-account backup maps personal and business to their own channels.
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.whatsapp_iphone_mount": (
        "angee.messaging_integrate_whatsapp.mount_extractor.WhatsAppPersonalMountExtractor"
    ),
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.whatsapp_smb_iphone_mount": (
        "angee.messaging_integrate_whatsapp.mount_extractor.WhatsAppBusinessMountExtractor"
    ),
}
"""Django settings contributed when the WhatsApp channel addon is installed."""
