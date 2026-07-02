"""Django config for Angee's parties addon."""

from __future__ import annotations

from django.apps import AppConfig


class PartiesConfig(AppConfig):
    """Source app manifest for contacts and handles."""

    default = True
    name = "angee.parties"

    def ready(self) -> None:
        """Run parties ready-time hooks after app population."""

        super().ready()
        # Phase-1 ready hooks belong here when parties needs populated models.
