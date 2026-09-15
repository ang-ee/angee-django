"""Settings contributed by the document extraction workflow addon."""

SETTINGS = {
    "ANGEE_OCR_ENGINE_CLASSES": {
        "inference": "angee.workflows_extraction.engines.InferenceMappingEngine",
        "inference_document": "angee.workflows_extraction.engines.InferenceDocumentEngine",
        "none": "angee.workflows_extraction.engines.NoOcrEngine",
    },
    "ANGEE_WORKFLOW_STEP_CLASSES.ocr_extract": "angee.workflows_extraction.steps.OcrExtractStepImpl",
    "ANGEE_OCR_MAX_BYTES": 25 * 1024 * 1024,
    "ANGEE_OCR_MAX_PAGES": 10,
    "ANGEE_OCR_MAX_EDGE": 3500,
    "ANGEE_OCR_DPI": 200,
    "ANGEE_OCR_TIMEOUT_SECONDS": 120,
    "ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS": None,
}
