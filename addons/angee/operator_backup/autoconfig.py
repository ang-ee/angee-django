"""Manual capture step registration and existing archive mount selection."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_WORKFLOW_STEP_CLASSES.operator_backup_capture": str,
        "ANGEE_WORKFLOW_STEP_CLASSES.operator_backup_publish": str,
        "ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE": str,
    },
)

SETTINGS: _Settings = {
    "ANGEE_WORKFLOW_STEP_CLASSES.operator_backup_capture": "angee.operator_backup.steps.Capture",
    "ANGEE_WORKFLOW_STEP_CLASSES.operator_backup_publish": "angee.operator_backup.steps.Publish",
    "ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE": "mount-operator-backups",
}
