"""Django config for Angee's IAM addon."""

from __future__ import annotations

from django.apps import AppConfig


class IAMConfig(AppConfig):
    """Source app manifest for Angee identity models."""

    default = True
    name = "angee.iam"

    def ready(self) -> None:
        """Wire IAM-owned REBAC relationships after app population."""

        super().ready()
        # App population phase 1 imports AppConfig before IAM cleanup wiring is ready.
        from angee.iam import signals

        signals.connect()
