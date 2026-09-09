"""Typed workflow step that journals only an extraction reference."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context

from angee.workflows.attempts import ArtifactSpec, RecoveryCapability, RecoveryMode
from angee.workflows.steps import StepEffect, StepImpl, StepOutcome, StepResult
from angee.workflows_ocr.service import extract, reextract


class OcrExtractInput(BaseModel):
    """Stable public references needed to perform extraction."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(min_length=1)
    model: str
    target_model: str
    target_id: str


class OcrExtractOutput(BaseModel):
    """Non-sensitive workflow journal projection."""

    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int


class OcrExtractConfig(BaseModel):
    """Schema and engine policy stored on the workflow definition."""

    model_config = ConfigDict(extra="forbid")
    schema: dict[str, Any] = Field(json_schema_extra={"widget": "json"})
    engine: str = "glm"
    engine_config: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"widget": "json"})


class OcrExtractStepImpl(StepImpl):
    """Create extraction evidence without copying its raw result into journals."""

    key = "ocr_extract"
    label = "Extract document evidence"
    category = "Activity"
    description = "Extract ordered stored files into schema-validated evidence."
    deterministic = False
    idempotent = True
    effect = StepEffect.EXTERNAL
    effect_description = "Reads files, calls the selected OCR engine, and writes evidence."
    input_model = OcrExtractInput
    output_model = OcrExtractOutput
    config_model = OcrExtractConfig
    outcomes = (
        StepOutcome("extracted", "Extracted"),
        StepOutcome("failed", "Failed", "Extraction evidence records a bounded processing failure."),
    )

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        del attempt
        return RecoveryCapability(RecoveryMode.FRESH)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        input_value = OcrExtractInput.model_validate(step_run.input)
        config = OcrExtractConfig.model_validate(step_run.step.config)
        actor = step_run.run.created_by
        if actor is None:
            raise ValueError("OCR extraction requires the workflow run actor.")
        with actor_context(actor):
            file_model = apps.get_model("storage", "File")
            model_model = apps.get_model("agents", "InferenceModel")
            requested = list(file_model.objects.filter(sqid__in=input_value.files))
            by_id = {str(item.sqid): item for item in requested}
            if any(file_id not in by_id for file_id in input_value.files):
                raise ValueError("One or more extraction files are unavailable.")
            files = [by_id[file_id] for file_id in input_value.files]
            target_model = apps.get_model(input_value.target_model)
            target = target_model.objects.get(sqid=input_value.target_id)
            inference_model = model_model.objects.get(sqid=input_value.model)
            evidence = extract(
                files=files,
                schema=config.schema,
                model=inference_model,
                authorized_target=target,
                engine=config.engine,
                config=config.engine_config,
            )
        return StepResult.done(
            output={"extraction_id": str(evidence.sqid), "revision": evidence.revision},
            outcome="extracted" if evidence.status == "succeeded" else "failed",
            artifacts=(ArtifactSpec(evidence, "Document extraction evidence"),),
        )

    def run_recovery(self, step_run: Any, *, now: datetime, source_attempt: Any, mode: RecoveryMode) -> StepResult:
        del now
        if mode is not RecoveryMode.FRESH or not isinstance(source_attempt.output, dict):
            raise ValueError("OCR recovery requires retained failed extraction output.")
        actor = step_run.run.created_by
        if actor is None:
            raise ValueError("OCR recovery requires the workflow run actor.")
        extraction_model = apps.get_model("workflows_ocr", "Extraction")
        with actor_context(actor):
            evidence = reextract(
                extraction_model.objects.get(sqid=source_attempt.output["extraction_id"])
            )
        return StepResult.done(
            output={"extraction_id": str(evidence.sqid), "revision": evidence.revision},
            outcome="extracted" if evidence.status == "succeeded" else "failed",
            artifacts=(ArtifactSpec(evidence, "Document extraction evidence"),),
        )
