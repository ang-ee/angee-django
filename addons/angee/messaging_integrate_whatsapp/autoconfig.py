"""Settings fragments contributed by the WhatsApp channel backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the WhatsApp backend into the channel backend registry. Dotted
    # key so it merges into messaging's default rather than replacing it.
    "ANGEE_CHANNEL_BACKEND_CLASSES.whatsapp": "angee.messaging_integrate_whatsapp.backend.WhatsAppChannelBackend",
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.whatsapp_iphone_backup": (
        "angee.messaging_integrate_whatsapp.extractor.WhatsAppIphoneBackupExtractor"
    ),
}
"""Django settings contributed when the WhatsApp channel addon is installed."""
