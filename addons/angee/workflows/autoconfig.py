"""Registry and periodic recovery settings contributed by workflows."""

SETTINGS = {
    "CELERY_BEAT_SCHEDULE:append": {
        "workflows.tick": {
            "task": "workflows.tick",
            "schedule": 15.0,
            "options": {"expires": 45},
        },
    },
    "ANGEE_WORKFLOW_STEP_CLASSES": {},
    "ANGEE_WORKFLOW_MAX_DISPATCHES": 20,
}
"""Django settings contributed when the workflows addon is installed."""
