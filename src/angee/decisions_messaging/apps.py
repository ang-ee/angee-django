"""Django config for the decision mail addon.

The addon uses ``ready()`` to listen for questions asked and reminders due, and to
register the check of its audience setting.
"""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class DecisionsMessagingConfig(AppConfig):
    """Source app manifest for the decision mail addon."""

    default = True
    name = "angee.decisions_messaging"

    def ready(self) -> None:
        """Connect the receivers after app population."""

        super().ready()
        from angee.decisions_messaging import signals
        from angee.decisions_messaging.checks import check_audiences

        signals.connect()
        checks.register(check_audiences, checks.Tags.compatibility)
