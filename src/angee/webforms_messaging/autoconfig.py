"""Settings fragments contributed by the web forms and messaging bridge."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_WEBFORM_TARGET_CLASSES.message": "angee.webforms_messaging.targets.MessageTarget",
    "ANGEE_CHANNEL_BACKEND_CLASSES.webform": "angee.webforms_messaging.backends.WebformChannelBackend",
}
