"""Django config for Angee's workflows addon."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


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
            connect_retained_decision_protection,
        )
        from angee.workflows.settlement import rebuild_subject_settlers
        from angee.workflows.triggers import connect_event_trigger_receiver

        checks.register(check_event_trigger_publishers, checks.Tags.database)
        checks.register(check_database_command_replay_declarations)
        connect_event_trigger_receiver()
        connect_retained_decision_protection()
        rebuild_subject_settlers()
