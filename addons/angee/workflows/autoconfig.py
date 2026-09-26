"""Settings fragments required by the workflows addon."""

from __future__ import annotations

SETTINGS = {
    "CELERY_BEAT_SCHEDULE:append": {
        "workflows.decisions": {
            "task": "workflows.decisions",
            "schedule": 60.0,
        },
        "workflows.reap": {
            "task": "workflows.reap",
            "schedule": 60.0,
        },
        "workflows.publish_dispatches": {
            "task": "workflows.publish_dispatches",
            "schedule": 30.0,
        },
        "workflows.schedule_triggers": {
            "task": "workflows.schedule_triggers",
            "schedule": 60.0,
        },
        "workflows.sweep": {
            "task": "workflows.sweep",
            "schedule": 60.0,
        },
    },
    # Step rows select behavior through registry keys, never dotted paths in row
    # data. Product addons contribute their own StepImpl subclasses under their
    # own keys through this same setting.
    "ANGEE_WORKFLOW_STEP_CLASSES": {
        "wait": "angee.workflows.steps.WaitStep",
        "gate": "angee.workflows.steps.GateStep",
        "map": "angee.workflows.steps.MapStep",
        "call_workflow": "angee.workflows.steps.CallWorkflow",
        "join_continuation": "angee.workflows.steps.JoinContinuation",
        "emit": "angee.workflows.steps.EmitStep",
    },
    "ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT": 300,
    "ANGEE_WORKFLOW_SUBJECT_SETTLERS": {},
}
"""Django settings contributed when the workflows addon is installed."""
