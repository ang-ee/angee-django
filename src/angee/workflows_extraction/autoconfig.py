"""Extraction workflow step registrations."""

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.prepare_pages": "angee.workflows_extraction.steps.PreparePagesStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.recognize_page": "angee.workflows_extraction.steps.RecognizePageStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.process_evidence": "angee.workflows_extraction.steps.ProcessEvidenceStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.infer_evidence": "angee.workflows_extraction.steps.InferEvidenceStep",
}
