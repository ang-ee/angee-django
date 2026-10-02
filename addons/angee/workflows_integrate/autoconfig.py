"""Contribute sync and archive steps through the native workflow registries."""

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.workflows_integrate.archive_steps.ArchiveExtractor"],
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES": {},
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_stream": "angee.workflows_integrate.steps.StreamStage",
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_rescan": "angee.workflows_integrate.steps.Rescan",
    "ANGEE_WORKFLOW_STEP_CLASSES.integrate_conflicts": "angee.workflows_integrate.steps.ConflictReview",
    "ANGEE_DECISION_ACTION_CLASSES.recheck_sync_conflict": "angee.workflows_integrate.steps.RecheckSyncConflict",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_probe": "angee.workflows_integrate.archive_steps.ArchiveProbe",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_gate": "angee.workflows_integrate.archive_steps.ArchiveGate",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_execute": "angee.workflows_integrate.archive_steps.ArchiveExecute",
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_summary": "angee.workflows_integrate.archive_steps.ArchiveSummary",
    "ANGEE_DECISION_ACTION_CLASSES.apply_archive_mappings": (
        "angee.workflows_integrate.archive_steps.ApplyArchiveMappings"
    ),
    "ANGEE_DECISION_ACTION_CLASSES.skip_archive": "angee.workflows_integrate.archive_steps.SkipArchive",
}
