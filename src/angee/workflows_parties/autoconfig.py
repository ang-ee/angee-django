"""Register the satellite's typed steps."""

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_review": "angee.workflows_parties.steps.IdentityReview",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": "angee.workflows_parties.steps.DedupeScan",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_review": "angee.workflows_parties.steps.DedupeReview",
}
