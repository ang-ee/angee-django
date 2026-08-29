"""Settings fragments required by the parties governance workflows."""

from __future__ import annotations

SETTINGS = {
    # Contributed into the workflows step registry with dotted keys, the
    # workflows_agents / workflows_integrate canon.
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": "angee.workflows_parties.steps.DedupeScanStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_gate": "angee.workflows_parties.steps.DedupeGateStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_execute": "angee.workflows_parties.steps.DedupeExecuteStepImpl",
}
