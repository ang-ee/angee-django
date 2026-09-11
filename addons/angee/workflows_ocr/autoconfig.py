"""Settings contributed by the document extraction workflow addon."""

SETTINGS = {
    "ANGEE_OCR_ENGINE_CLASSES": {
        "none": "angee.workflows_ocr.engines.NoOcrEngine",
    },
    "ANGEE_WORKFLOW_STEP_CLASSES.ocr_extract": "angee.workflows_ocr.steps.OcrExtractStepImpl",
    "ANGEE_OCR_MAX_BYTES": 25 * 1024 * 1024,
    "ANGEE_OCR_MAX_PAGES": 10,
    "ANGEE_OCR_MAX_EDGE": 3500,
    "ANGEE_OCR_DPI": 200,
    "ANGEE_OCR_TIMEOUT_SECONDS": 120,
}
