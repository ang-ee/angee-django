"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    name = "angee.integrate"

    def ready(self) -> None:
        """Wire integration-owned denormalization maintenance after app population."""

        super().ready()
        # Signals resolve concrete models after Django app population.
        from angee.integrate import signals

        signals.connect()
