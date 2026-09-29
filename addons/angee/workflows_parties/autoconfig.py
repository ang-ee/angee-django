"""Register the satellite's typed steps and decision actions."""

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_identity_review": "angee.workflows_parties.steps.IdentityReview",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_scan": "angee.workflows_parties.steps.DedupeScan",
    "ANGEE_WORKFLOW_STEP_CLASSES.parties_dedupe_review": "angee.workflows_parties.steps.DedupeReview",
    "ANGEE_DECISION_ACTION_CLASSES.apply_identity": "angee.workflows_parties.steps.ApplyIdentity",
    "ANGEE_DECISION_ACTION_CLASSES.reject_identity": "angee.workflows_parties.steps.RejectIdentity",
    "ANGEE_DECISION_ACTION_CLASSES.escalate_identity": "angee.workflows_parties.steps.EscalateIdentity",
    "ANGEE_DECISION_ACTION_CLASSES.merge_parties": "angee.workflows_parties.steps.MergeParties",
    "ANGEE_DECISION_ACTION_CLASSES.keep_parties_separate": "angee.workflows_parties.steps.KeepPartiesSeparate",
    "ANGEE_DECISION_ACTION_CLASSES.skip_duplicate_pair": "angee.workflows_parties.steps.SkipDuplicatePair",
}
