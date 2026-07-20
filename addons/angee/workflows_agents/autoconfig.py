"""Settings fragments required by the workflows-agents composition addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the ``agent`` activity step into the workflows registry without
    # editing the workflows addon. Dotted-key autoconfig deep-merges this key into
    # ANGEE_WORKFLOW_STEP_CLASSES, matching other composition addons' registry
    # contributions.
    "ANGEE_WORKFLOW_STEP_CLASSES.agent": "angee.workflows_agents.steps.AgentStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.agent_session": "angee.workflows_agents.steps.AgentSessionStepImpl",
}
"""Django settings contributed when the workflows-agents addon is installed."""
