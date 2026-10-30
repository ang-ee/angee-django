"""Django configuration for the intake addon."""

from __future__ import annotations

from django.apps import AppConfig


class IntakeConfig(AppConfig):
    """Own request evidence and capture from messaging into work triage."""

    default = True
    name = "angee.intake"

    def ready(self) -> None:
        """Wire capture onto messaging's one declared ingest event."""

        super().ready()
        from angee.intake import signals

        signals.connect()
