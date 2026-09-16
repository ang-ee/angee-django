"""Settings contributed by the document extraction workflow addon."""

SETTINGS = {
    "ANGEE_OCR_ENGINE_CLASSES": {
        "inference": "angee.workflows_extraction.engines.InferenceMappingEngine",
        "none": "angee.workflows_extraction.engines.NoOcrEngine",
    },
    "ANGEE_WORKFLOW_STEP_CLASSES.prepare_pages": "angee.workflows_extraction.steps.PreparePagesStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.recognize_page": "angee.workflows_extraction.steps.RecognizePageStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.collect_carriers": "angee.workflows_extraction.steps.CollectCarriersStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.process_evidence": "angee.workflows_extraction.steps.ProcessEvidenceStepImpl",
    "ANGEE_WORKFLOW_STEP_CLASSES.infer_evidence": "angee.workflows_extraction.steps.InferEvidenceStepImpl",
    "ANGEE_OCR_MAX_BYTES": 25 * 1024 * 1024,
    "ANGEE_OCR_MAX_PAGES": 10,
    "ANGEE_OCR_MAX_EDGE": 3500,
    "ANGEE_OCR_DPI": 200,
    "ANGEE_OCR_TIMEOUT_SECONDS": 120,
    "ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS": None,
}
