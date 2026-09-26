"""Settings contributed by the document extraction workflow addon."""

_EXTRACTION_PROFILE_CLASSES = {
    "none": "angee.workflows_extraction.profiles.UnconfiguredExtractionProfile",
}

SETTINGS = {
    "ANGEE_EXTRACTION_PROFILE_CLASSES": _EXTRACTION_PROFILE_CLASSES,
    "ANGEE_WORKFLOW_STEP_CLASSES.prepare_pages": "angee.workflows_extraction.steps.PreparePagesStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.recognize_page": "angee.workflows_extraction.steps.RecognizePageStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.collect_carriers": "angee.workflows_extraction.steps.CollectCarriersStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.process_evidence": "angee.workflows_extraction.steps.ProcessEvidenceStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.infer_evidence": "angee.workflows_extraction.steps.InferEvidenceStepImpl",
    "ANGEE_EXTRACTION_MAX_BYTES": 25 * 1024 * 1024,
    "ANGEE_EXTRACTION_TIMEOUT_SECONDS": 120,
}
