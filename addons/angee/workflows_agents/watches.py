"""Observe ACP status saves without exposing a workflow trigger."""

from angee.workflows.watches import RecordWatch


class AgentTurnWatch(RecordWatch):
    """Transcript batches do not change the waiting turn predicate."""

    key = "agent_turn"
    label = "Agent turn status"
    model_label = "agents.AgentTurn"
    fields = ("status",)
