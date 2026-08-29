"""Django config for Angee's workflows addon."""

from __future__ import annotations

from collections.abc import Callable

from django.apps import AppConfig
from django.core import checks

_CHECKS_REGISTERED = False


class WorkflowsConfig(AppConfig):
    """Source app manifest for workflow definition models."""

    default = True
    name = "angee.workflows"

    def ready(self) -> None:
        """Run workflows ready-time hooks after app population."""

        super().ready()
        from angee.workflows.models import check_event_trigger_change_publishers
        from angee.workflows.triggers import connect_event_trigger_receiver

        _register_checks(check_event_trigger_change_publishers)
        connect_event_trigger_receiver()


def _register_checks(*functions: Callable[..., list[checks.CheckMessage]]) -> None:
    """Register workflow checks once per process.

    The event-trigger check queries the trigger table, so it registers under
    ``Tags.database`` — Django runs database-tagged checks only for ``migrate``
    and ``check --database``, when a database is expected, instead of on every
    ``manage.py`` invocation.
    """

    global _CHECKS_REGISTERED
    if _CHECKS_REGISTERED:
        return
    for function in functions:
        checks.register(checks.Tags.database)(function)
    _CHECKS_REGISTERED = True
