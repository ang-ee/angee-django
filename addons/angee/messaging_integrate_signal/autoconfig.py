"""Settings fragments contributed by the Signal channel addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_CHANNEL_BACKEND_CLASSES.signal": ("angee.messaging_integrate_signal.backend.SignalChannelBackend"),
    "SIGNAL_CLI_BIN": "signal-cli",
}
"""Django settings contributed when the Signal channel addon is installed."""
