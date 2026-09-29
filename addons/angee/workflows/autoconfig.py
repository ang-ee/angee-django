"""Registry and periodic recovery settings contributed by workflows."""

SETTINGS = {
    "CELERY_BEAT_SCHEDULE:append": {
        "workflows.tick": {
            "task": "workflows.tick",
            "schedule": 15.0,
            "options": {"expires": 45},
        },
    },
    "ANGEE_WORKFLOW_STEP_CLASSES": {
        "review": "angee.workflows.reviews.Review",
        "map": "angee.workflows.maps.Map",
        "await_run": "angee.workflows.awaits.AwaitRun",
    },
    "ANGEE_WORKFLOW_MAP_CONCURRENCY": 10,
    "ANGEE_WORKFLOW_MAX_DISPATCHES": 20,
    "ANGEE_WORKFLOW_RETENTION_DAYS": 90,
}
"""Django settings contributed when the workflows addon is installed."""
