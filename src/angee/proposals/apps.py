"""Django configuration for the proposals addon."""

from __future__ import annotations

from django.apps import AppConfig


class ProposalsConfig(AppConfig):
    """Own PM-backed solicitation, comparison, disclosure, and decision."""

    default = True
    name = "angee.proposals"

    def ready(self) -> None:
        """Bind deletion enforcement after the model registry is populated."""

        super().ready()
        from angee.proposals.signals import connect

        connect()
