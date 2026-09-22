"""Explicit execution and subject-settlement contributions."""

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_stream": "angee.workflows_integrate.steps.BoundedStreamStage",
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_coverage": "angee.workflows_integrate.steps.CoverageGate",
    "ANGEE_BRIDGE_SYNC_DISPATCH": "angee.workflows_integrate.admission.dispatch_bridge_cycle",
    "ANGEE_WORKFLOW_SUBJECT_SETTLERS:append": {
        "angee.integrate.models.Bridge": "angee.workflows_integrate.settle.settle_bridge_run",
    },
}
