"""Contribute archive extractor, step and review action registrations."""

SETTINGS = {
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES": {},
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_probe": "angee.workflows_integrate.archive_steps.ArchiveProbe",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_gate": "angee.workflows_integrate.archive_steps.ArchiveGate",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_execute": "angee.workflows_integrate.archive_steps.ArchiveExecute",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_summary": "angee.workflows_integrate.archive_steps.ArchiveSummary",
    "ANGEE_DECISION_ACTION_CLASSES.apply_archive_mappings": (
        "angee.workflows_integrate.archive_steps.ApplyArchiveMappings"
    ),
    "ANGEE_DECISION_ACTION_CLASSES.skip_archive": "angee.workflows_integrate.archive_steps.SkipArchive",
}
