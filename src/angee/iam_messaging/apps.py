"""Django config for Angee's emailed sign-in link addon."""

from __future__ import annotations

from django.apps import AppConfig


class IamMessagingConfig(AppConfig):
    """Django app configuration for emailed sign-in links."""

    default = True
    name = "angee.iam_messaging"

    def ready(self) -> None:
        """Retire a deactivated account's links once the user model is loaded."""

        # Deferred to ready(): signal wiring after app population.
        from angee.iam_messaging import signals

        signals.connect()
