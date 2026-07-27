"""Settings fragments contributed by the Facebook takeout addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_CHANNEL_BACKEND_CLASSES.facebook": (
        "angee.messaging_integrate_facebook.backend.FacebookChannelBackend"
    ),
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.facebook_takeout": (
        "angee.messaging_integrate_facebook.extractor.FacebookTakeoutExtractor"
    ),
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.facebook_takeout_mount": (
        "angee.messaging_integrate_facebook.mount_extractor.FacebookMountTakeoutExtractor"
    ),
}
"""Django settings contributed when the Facebook takeout addon is installed."""
