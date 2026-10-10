"""Django config for Angee's IAM addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"

    def ready(self) -> None:
        """Connect IAM's receivers once Django's auth app has connected its own."""

        # Deferred to ready(): signal wiring after app population.
        from angee.iam import deployment, signals

        signals.connect()
        deployment.connect()
