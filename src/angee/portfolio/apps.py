"""Django configuration for the portfolio addon."""

from __future__ import annotations

from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    """Own products, initiatives, health reporting, and release artifacts."""

    default = True
    name = "angee.portfolio"

    def ready(self) -> None:
        """Bind the report projection receivers once the models are populated."""

        from angee.portfolio import signals

        signals.connect()
