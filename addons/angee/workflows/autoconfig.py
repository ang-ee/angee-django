"""Registry and periodic recovery settings contributed by workflows."""

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.workflows.steps.Step", "angee.workflows.triggers.TriggerSource"],
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
        "ask_decision": "angee.workflows.reviews.AskDecision",
        "map": "angee.workflows.maps.Map",
        "await_run": "angee.workflows.awaits.AwaitRun",
    },
    "ANGEE_WORKFLOW_MAP_CONCURRENCY": 10,
    "ANGEE_WORKFLOW_MAX_DISPATCHES": 20,
    "ANGEE_WORKFLOW_RETENTION_DAYS": 90,
}
"""Django settings contributed when the workflows addon is installed."""
