"""Extraction profile, provider and workflow registrations."""

SETTINGS = {
    "ANGEE_EXTRACTION_PROFILE_CLASSES": {"none": "angee.workflows_extraction.profiles.UnconfiguredExtractionProfile"},
    "ANGEE_EXTRACTION_PROVIDER_CLASSES": {"native": "angee.workflows_extraction.providers.NativeExtractionProvider"},
    "ANGEE_WORKFLOW_STEP_CLASSES.prepare_pages": "angee.workflows_extraction.steps.PreparePagesStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.recognize_page": "angee.workflows_extraction.steps.RecognizePageStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.collect_carriers": "angee.workflows_extraction.steps.CollectCarriersStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.process_evidence": "angee.workflows_extraction.steps.ProcessEvidenceStep",
    "ANGEE_WORKFLOW_STEP_CLASSES.infer_evidence": "angee.workflows_extraction.steps.InferEvidenceStep",
    "ANGEE_EXTRACTION_MAX_BYTES": 25 * 1024 * 1024,
}
