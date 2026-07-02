"""Django config for the workflows-agents composition addon."""

from __future__ import annotations

from django.apps import AppConfig


class WorkflowsAgentsConfig(AppConfig):
    """Source app manifest for workflow-agent composition seams."""

    default = True
    name = "angee.workflows_agents"

    def ready(self) -> None:
        """Run workflows-agents ready-time hooks after app population."""

        super().ready()
        # Phase-1 ready hooks belong here when this composition addon needs them.
