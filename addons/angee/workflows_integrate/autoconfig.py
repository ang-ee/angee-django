"""Settings fragments required by the workflows-integrate composition addon."""

from __future__ import annotations

SETTINGS = {
    # Concrete vendor/domain addons contribute extractor implementations with
    # dotted keys, exactly as workflow step implementations are composed.
    "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES": {},
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_probe": (
        "angee.workflows_integrate.steps.ArchiveProbeStepImpl"
    ),
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_gate": (
        "angee.workflows_integrate.steps.ArchiveGateStepImpl"
    ),
    "ANGEE_WORKFLOW_STEP_CLASSES.archive_execute": (
        "angee.workflows_integrate.steps.ArchiveExecuteStepImpl"
    ),
}
"""Django settings contributed when the workflows-integrate addon is installed."""
