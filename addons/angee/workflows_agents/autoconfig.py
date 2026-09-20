"""Settings fragments required by the workflows-agents composition addon."""

from __future__ import annotations

SETTINGS = {
    # Contribute the ``infer`` activity step into the workflows registry without
    # editing the workflows addon. Dotted-key autoconfig deep-merges this key into
    # ANGEE_WORKFLOW_STEP_CLASSES, matching other composition addons' registry
    # contributions.
    "ANGEE_WORKFLOW_STEP_CLASSES.infer": "angee.workflows_agents.steps.InferStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.agent_session": "angee.workflows_agents.steps.AgentSessionStepImpl",
    "ANGEE_AGENT_TEARDOWN_HOOKS:append": [
        "angee.workflows_agents.sessions.close_agent_sessions",
    ],
}
"""Django settings contributed when the workflows-agents addon is installed."""
