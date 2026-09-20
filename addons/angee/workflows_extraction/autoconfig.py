"""Settings contributed by the document extraction workflow addon."""

_EXTRACTION_ENGINE_CLASSES = {
    "inference": "angee.workflows_extraction.engines.InferenceMappingEngine",
    "none": "angee.workflows_extraction.engines.NoExtractionEngine",
}

SETTINGS = {
    "ANGEE_EXTRACTION_ENGINE_CLASSES": _EXTRACTION_ENGINE_CLASSES,
    "ANGEE_WORKFLOW_STEP_CLASSES.prepare_pages": "angee.workflows_extraction.steps.PreparePagesStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.recognize_page": "angee.workflows_extraction.steps.RecognizePageStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.collect_carriers": "angee.workflows_extraction.steps.CollectCarriersStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.process_evidence": "angee.workflows_extraction.steps.ProcessEvidenceStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.infer_evidence": "angee.workflows_extraction.steps.InferEvidenceStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.revise_evidence": "angee.workflows_extraction.steps.ReviseEvidenceStepImpl",
    "ANGEE_EXTRACTION_MAX_BYTES": 25 * 1024 * 1024,
    "ANGEE_EXTRACTION_MAX_PAGES": 10,
    "ANGEE_EXTRACTION_MAX_EDGE": 3500,
    "ANGEE_EXTRACTION_DPI": 200,
    "ANGEE_EXTRACTION_TIMEOUT_SECONDS": 120,
}
