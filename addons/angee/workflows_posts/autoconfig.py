"""Contribute comment workflow steps and protect workflow-written provenance."""

from typing import TypedDict

from angee.workflows_posts.constants import AGENT_TURN_METADATA_KEY

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_WORKFLOW_STEP_CLASSES.check_replied": str,
        "ANGEE_WORKFLOW_STEP_CLASSES.schedule_reply": str,
        "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS:append": list[str],
    },
)

SETTINGS: _Settings = {
    "ANGEE_WORKFLOW_STEP_CLASSES.check_replied": "angee.workflows_posts.steps.CheckReplied",
    "ANGEE_WORKFLOW_STEP_CLASSES.schedule_reply": "angee.workflows_posts.steps.ScheduleReply",
    "ANGEE_MESSAGING_PROTECTED_LOCAL_KEYS:append": [AGENT_TURN_METADATA_KEY],
}
