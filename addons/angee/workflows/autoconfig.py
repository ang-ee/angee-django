"""Registry and periodic recovery settings contributed by workflows."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_IMPL_REGISTRIES:append": list[str],
        "ANGEE_MERGE_GUARDS:append": list[str],
        "ANGEE_TIMELINE_KINDS:append": list[str],
        "ANGEE_RESOURCE_ENTRY_KIND_CLASSES:append": dict[str, str],
        "ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES": dict[str, str],
        "CELERY_BEAT_SCHEDULE:append": dict[str, dict[str, object]],
        "ANGEE_WORKFLOW_STEP_CLASSES": dict[str, str],
        "ANGEE_WORKFLOW_MAP_CONCURRENCY": int,
        "ANGEE_WORKFLOW_MAX_DISPATCHES": int,
        "ANGEE_WORKFLOW_RETENTION_DAYS": int,
    },
)

SETTINGS: _Settings = {
    "ANGEE_IMPL_REGISTRIES:append": [
        "angee.workflows.steps.Step", "angee.workflows.triggers.TriggerSource",
    ],
    "ANGEE_MERGE_GUARDS:append": ["angee.workflows.managers.unfinished_run_merge_guard"],
    "ANGEE_TIMELINE_KINDS:append": ["angee.workflows.timeline.timeline_kinds"],
    "ANGEE_RESOURCE_ENTRY_KIND_CLASSES:append": {
        "workflows.workflowextension": "angee.workflows.resources.WorkflowExtensionResourceKind",
    },
    "ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES": {
        "record_changed": "angee.workflows.triggers.RecordChanged",
    },
    "CELERY_BEAT_SCHEDULE:append": {
        "workflows.tick": {
            "task": "workflows.tick",
            "schedule": 15.0,
            "options": {"expires": 45},
        },
    },
    "ANGEE_WORKFLOW_STEP_CLASSES": {
        "ask_decision": "angee.workflows.decision_steps.AskDecision",
        "map": "angee.workflows.maps.Map",
        "await_run": "angee.workflows.awaits.AwaitRun",
    },
    "ANGEE_WORKFLOW_MAP_CONCURRENCY": 10,
    "ANGEE_WORKFLOW_MAX_DISPATCHES": 20,
    "ANGEE_WORKFLOW_RETENTION_DAYS": 90,
}
"""Django settings contributed when the workflows addon is installed."""
