"""Archive extraction, stream execution and subject-settlement contributions."""

SETTINGS = {
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES": {},
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_probe": "angee.workflows_integrate.steps.ArchiveProbeStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_gate": "angee.workflows_integrate.steps.ArchiveGateStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_execute": "angee.workflows_integrate.steps.ArchiveExecuteStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_stream": "angee.workflows_integrate.steps.BoundedStreamStage",
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_coverage": "angee.workflows_integrate.steps.CoverageGate",
    "ANGEE_BRIDGE_SYNC_DISPATCH": "angee.workflows_integrate.admission.dispatch_bridge_cycle",
    "ANGEE_WORKFLOW_SUBJECT_SETTLERS:append": {
        "angee.integrate.models.Bridge": "angee.workflows_integrate.settle.settle_bridge_run",
    },
}
