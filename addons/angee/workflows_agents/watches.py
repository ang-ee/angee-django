"""Observe ACP status saves without exposing a workflow trigger."""

from angee.workflows.triggers import TriggerSource


class AgentTurnWatch(TriggerSource):
    """Transcript batches do not change the waiting turn predicate."""

    key = "agent_turn"
    label = "Agent turn status"
    model_label = "agents.AgentTurn"
    fields = ("status",)
    watch_only = True
