"""Django configuration for the portfolio addon."""

from __future__ import annotations

from django.apps import AppConfig


class PortfolioConfig(AppConfig):
    """Own products, initiatives, health reporting, and release artifacts."""

    default = True
    name = "angee.portfolio"

    def ready(self) -> None:
        """Connect report projection hooks after model population."""

        from angee.portfolio.signals import connect

        connect()
