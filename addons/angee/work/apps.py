"""Django configuration for the work addon."""

from __future__ import annotations

from django.apps import AppConfig


class WorkConfig(AppConfig):
    """Own queues, cycles, triage, and their same-row task contribution."""

    default = True
    name = "angee.work"

    def ready(self) -> None:
        """Wire work's response to the upstream chatter activity seam."""

        super().ready()
        # App population imports AppConfig before models; defer signal wiring.
        from angee.work import signals

        signals.connect()
