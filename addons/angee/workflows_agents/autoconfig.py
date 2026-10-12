"""Compose conversation steps and transactional turn observation."""

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.start_conversation": "angee.workflows_agents.steps.StartConversation",
    "ANGEE_WORKFLOW_STEP_CLASSES.close_conversation": "angee.workflows_agents.steps.CloseConversation",
    "ANGEE_WORKFLOW_WATCH_CLASSES.agent_turn": "angee.workflows_agents.watches.AgentTurnWatch",
}
