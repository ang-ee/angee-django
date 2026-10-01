"""Settings fragments contributed when the intake addon is installed."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_DECISION_ACTION_CLASSES.approve": "angee.intake.models.ApproveNeedAccess",
    "ANGEE_DECISION_ACTION_CLASSES.deny": "angee.intake.models.DenyNeedAccess",
    "ANGEE_WORK_MERGE_CONTRIBUTORS:append": [
        "angee.intake.merge.move_task_needs",
    ],
}
