"""Django config for the decision mail addon.

The addon uses ``ready()`` to listen for questions asked and reminders due.
"""

from __future__ import annotations

from django.apps import AppConfig


class DecisionsMessagingConfig(AppConfig):
    """Source app manifest for the decision mail addon."""

    default = True
    name = "angee.decisions_messaging"

    def ready(self) -> None:
        """Connect the receivers after app population."""

        super().ready()
        from angee.decisions_messaging import signals

        signals.connect()
