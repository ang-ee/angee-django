"""Settings fragments contributed by the IMAP channel backend addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the IMAP backend into the channel backend registry. Dotted key
    # so it merges into messaging's default rather than replacing it.
    "ANGEE_CHANNEL_BACKEND_CLASSES.imap": "angee.messaging_integrate_imap.backend.ImapChannelBackend",
}
