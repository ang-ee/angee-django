"""Django config for Angee's knowledge addon."""

from __future__ import annotations

from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    """Source app manifest for knowledge indexes and record bindings."""

    default = True
    name = "angee.knowledge"

    def ready(self) -> None:
        """Wire knowledge-owned signal receivers after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before the models exist; defer.
        from angee.knowledge import signals

        signals.connect()
