"""Settings fragments contributed by the Telegram channel addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_CHANNEL_BACKEND_CLASSES.telegram": (
        "angee.messaging_integrate_telegram.backend.TelegramChannelBackend"
    ),
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES.telegram_takeout": (
        "angee.messaging_integrate_telegram.extractor.TelegramTakeoutExtractor"
    ),
}
"""Django settings contributed when the Telegram channel addon is installed."""
