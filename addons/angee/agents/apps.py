"""Django config for Angee's agents addon."""

from __future__ import annotations

from django.apps import AppConfig


class AgentsConfig(AppConfig):
    """Source app manifest for agent catalogue models."""

    default = True
    name = "angee.agents"

    def ready(self) -> None:
        """Run agents ready-time hooks after app population."""

        super().ready()
        # Phase-1 ready hooks belong here when agents needs populated models.
