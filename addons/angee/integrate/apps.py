"""Django config for Angee's integration runtime addon."""

from __future__ import annotations

from collections.abc import Callable

from django.apps import AppConfig
from django.core import checks

_CHECKS_REGISTERED = False


class IntegrateConfig(AppConfig):
    """Source app manifest for Angee integration runtime primitives."""

    default = True
    name = "angee.integrate"

    def ready(self) -> None:
        """Wire integration-owned denormalization maintenance after app population."""

        super().ready()
        # Phase-1 ready hooks import after app population: signals and checks
        # both resolve concrete models from Django's loaded registry.
        from angee.integrate import signals
        from angee.integrate.models import check_credential_disconnect_guards
        from angee.integrate.registry import check_source_kind_contracts

        signals.connect()
        _register_checks(
            check_source_kind_contracts,
            check_credential_disconnect_guards,
        )


def _register_checks(*functions: Callable[..., list[checks.CheckMessage]]) -> None:
    """Register integrate checks once per process."""

    global _CHECKS_REGISTERED
    if _CHECKS_REGISTERED:
        return
    for function in functions:
        checks.register(checks.Tags.models)(function)
    _CHECKS_REGISTERED = True
