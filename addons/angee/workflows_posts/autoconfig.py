"""Contribute comment workflow steps and protect workflow-written provenance."""

from angee.workflows_posts.constants import AGENT_TURN_METADATA_KEY

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.check_replied": "angee.workflows_posts.steps.CheckReplied",
    "ANGEE_WORKFLOW_STEP_CLASSES.schedule_reply": "angee.workflows_posts.steps.ScheduleReply",
    "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS:append": [AGENT_TURN_METADATA_KEY],
}
