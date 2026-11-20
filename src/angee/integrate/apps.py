"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    name = "angee.integrate"

    def ready(self) -> None:
        """Register the credential permission check and connect integrate's model receivers after app population."""

        from angee.integrate import signals
        from angee.integrate.checks import check_replace_secret_permissions

        checks.register(check_replace_secret_permissions, checks.Tags.security)
        signals.connect()
