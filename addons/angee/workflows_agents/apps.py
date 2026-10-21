"""Connect run-owned ACP resource cleanup to the workflow engine."""

from typing import Any

from django.apps import AppConfig, apps
from rebac import system_context

from angee.workflows.signals import isolated_run_cleanup, run_stopping


@isolated_run_cleanup
def close_run_conversations(sender: Any, *, run: Any, **kwargs: Any) -> None:
    """Close created sessions and stop created turns on explicitly continued sessions."""
    sessions = apps.get_model("agents", "AgentSession")
    turns = apps.get_model("agents", "AgentTurn")
    records = apps.get_model("workflows", "StepRecord")
    content_types = apps.get_model("contenttypes", "ContentType")
    with system_context(reason="workflows_agents.run_stopping"):
        created = records.system_queryset().filter(run=run, operation="created")
        owned = created.filter(content_type=content_types.objects.get_for_model(sessions)).values("object_id")
        sessions.objects.close_as_system(
            sessions.system_queryset().filter(pk__in=owned), reason="workflows_agents.run_stopping",
        )
        own_turns = created.filter(content_type=content_types.objects.get_for_model(turns)).values("object_id")
        open_turns = (
            turns.objects.open()
            .system_context(reason="workflows_agents.run_stopping")
            .filter(pk__in=own_turns)
            .select_related("session")
            .order_by("session_id", "index")
        )
        for turn in open_turns:
            turn.session.cancel_turn(turn)


class WorkflowsAgentsConfig(AppConfig):
    """Attach conversation cleanup without a runtime registry."""

    name = "angee.workflows_agents"
    default = True

    def ready(self) -> None:
        """Connect once after concrete models have been composed."""
        run_stopping.connect(close_run_conversations, dispatch_uid="workflows_agents.run_stopping")
