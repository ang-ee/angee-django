"""Settings fragments contributed when the intake addon is installed."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_WORK_MERGE_CONTRIBUTORS:append": [
        "angee.intake.merge.move_task_needs",
    ],
}
