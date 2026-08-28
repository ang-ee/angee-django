"""Django config for the Angee money addon.

Money uses ``ready()`` for the process-local reference-currency settings check.
Its value field registers its GraphQL scalar at field-module import.
"""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.conf import settings
from django.core import checks

_CHECKS_REGISTERED = False


class MoneyConfig(AppConfig):
    """Source app manifest for the Angee money addon."""

    default = True
    name = "angee.money"

    def ready(self) -> None:
        """Register money-owned process hooks after app population."""

        super().ready()
        _register_checks()


def _register_checks() -> None:
    """Register money checks once per process."""

    global _CHECKS_REGISTERED
    if _CHECKS_REGISTERED:
        return
    checks.register(check_reference_currency_setting)
    _CHECKS_REGISTERED = True


def check_reference_currency_setting(
    app_configs: Sequence[AppConfig] | None, **kwargs: object
) -> list[checks.CheckMessage]:
    """Warn when the required reference currency setting is unset.

    A **Warning, not an Error**, by design: the reference currency is only needed
    once a project converts between currencies, so a single-currency install is
    legitimately usable before the setting is chosen. An Error would break
    ``migrate`` / ``schema --check`` for every such
    install; the hard gate lives where conversion actually happens
    (:func:`~angee.money.models.reference_currency_code` raises
    ``ImproperlyConfigured`` from the rate manager). This check makes the pending
    choice visible without blocking the build.
    """

    from angee.money.models import REFERENCE_CURRENCY_SETTING

    if getattr(settings, REFERENCE_CURRENCY_SETTING, None):
        return []
    return [
        checks.Warning(
            f"{REFERENCE_CURRENCY_SETTING} is not set.",
            hint=(
                f"Set {REFERENCE_CURRENCY_SETTING} to the ISO-4217 code all currency rates are "
                "relative to. Currency conversion raises ImproperlyConfigured until it is set; "
                "single-currency use does not need it."
            ),
            id="angee.money.W001",
        )
    ]
