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
        from angee.workflows.models import (
            check_database_command_replay_declarations,
            check_event_trigger_publishers,
        )
        from angee.workflows.settlement import subject_settlement_handlers
        from angee.workflows.triggers import connect_event_trigger_receiver

        _register_checks(check_event_trigger_publishers, check_database_command_replay_declarations)
        connect_event_trigger_receiver()
        subject_settlement_handlers.cache_clear()
        subject_settlement_handlers()


def _register_checks(*functions: Callable[..., list[checks.CheckMessage]]) -> None:
    """Register workflow checks once per process.

    The event-trigger check queries the trigger table and is classified under
    ``Tags.database`` for explicit tag selection. Unfiltered system checks also
    include it; the tag does not restrict it to commands given ``--database``.
    """

    global _CHECKS_REGISTERED
    if _CHECKS_REGISTERED:
        return
    for function in functions:
        checks.register(checks.Tags.database)(function)
    _CHECKS_REGISTERED = True
