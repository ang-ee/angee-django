"""Django config for Angee's spaces addon."""

from __future__ import annotations

from django.apps import AppConfig


class SpacesConfig(AppConfig):
    """Source app manifest for shared groups and their canonical rosters."""

    default = True
    name = "angee.spaces"

    def ready(self) -> None:
        """Wire spaces-owned lifecycle receivers after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.spaces import signals

        signals.connect()
