"""Typed workflow step that journals only an extraction reference."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.impl import resolve_impl_class
from angee.base.refs import canonical_record_target
from angee.base.serialization import canonical_json_sha256
from angee.workflows.attempts import (
    ArtifactSpec,
    DecisionGateOutput,
    ExternalOperationPolicy,
    RecoveryCapability,
    RecoveryMode,
)
from angee.workflows.engine import external_operation_request
from angee.workflows.steps import (
    DecisionApplyStep,
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
)
from angee.workflows_extraction.engines import (
    RETAINED_CARRIER_UNAVAILABLE,
    DocumentPipelineError,
    ExtractionEngine,
    PageImage,
)
from angee.workflows_extraction.service import (
    CorrectionBasis,
    SupersededInference,
    collect_carriers,
    correction_basis,
    infer,
    prepare_pages,
    process,
    require_approved_model_deployment,
    restore_prepared_pages,
    retain_correction_revision,
)

EngineConfig = Annotated[dict[str, Any], Field(json_schema_extra={"widget": "json"})]


class ExtractionEngineConfigInput(BaseModel):
    """Per-invocation extraction-engine configuration."""

    model_config = ConfigDict(extra="forbid")
    engine_config: EngineConfig = Field(default_factory=dict)


class ExtractionPolicyInput(ExtractionEngineConfigInput):
    """Per-invocation schema and profile policy for evidence processing."""

    schema_: dict[str, Any] = Field(alias="schema", json_schema_extra={"widget": "json"})
    engine: str = Field(min_length=1)


class ExtractionSourceInput(BaseModel):
    """Stable public source and target references needed to perform extraction."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(default_factory=list)
    message_parts: list[str] = Field(default_factory=list)
    target_model: str
    target_id: str


class ExtractionOutput(BaseModel):
    """Non-sensitive workflow journal projection."""

    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int


class PreparePagesInput(ExtractionEngineConfigInput, ExtractionSourceInput):
    """The original source/target refs; provider work happens later."""

    model: str | None = None
    recognition_model: str | None = None


class PreparePagesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest: dict[str, Any]
    recognition_pages: list[dict[str, Any]]
    recognition_model_id: str
    mapping_model_id: str
    recognition_config_digest: str
    target_model: str
    target_id: str


class PreparePagesStepImpl(StepImpl):
    key = "prepare_pages"
    label = "Prepare every document page"
    category = "Activity"
    deterministic = True
    idempotent = True
    effect = StepEffect.WRITE
    effect_description = "Stores bounded native and raster carriers as READY Files."
    input_model = PreparePagesInput
    output_model = PreparePagesOutput
    outcomes = (StepOutcome("prepared", "Prepared"),)

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        value = self.validate_input(step_run.input)
        options = value.engine_config
        actor = step_run.run.admission_actor()
        if actor is None:
            raise PermissionDenied("Page preparation requires the workflow actor.")
        with actor_context(actor):
            files, parts, target = _resolve_sources(value)
            prepared = prepare_pages(
                files=files, message_parts=parts, authorized_target=target, config=options,
            )
        model_id = value.recognition_model or ""
        recognition_options = dict(options.get("recognition_config") or {})
        config_digest = canonical_json_sha256(recognition_options)
        recognition_pages = [
            {
                "source_position": page.source_position,
                "page_position": page.page_position,
                "image_file_id": str(page.recognition_file.sqid),
                "image_digest": str(page.recognition_file.content_hash),
                "width": page.recognition_image.width,
                "height": page.recognition_image.height,
                "dpi": page.recognition_image.dpi,
                "model_id": model_id,
                "config_digest": config_digest,
            }
            for page in prepared.recognition_pages
        ]
        return StepResult.done(output={
            "manifest": prepared.manifest,
            "recognition_pages": recognition_pages,
            "recognition_model_id": model_id,
            "mapping_model_id": value.model or "",
            "recognition_config_digest": config_digest,
            "target_model": value.target_model,
            "target_id": value.target_id,
        }, outcome="prepared")


class RecognizePageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_position: int = Field(ge=0)
    page_position: int = Field(ge=0)
    image_file_id: str
    image_digest: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    dpi: int = Field(gt=0)
    model_id: str
    config_digest: str
    engine: str = Field(default="inference", min_length=1)
    engine_config: EngineConfig = Field(default_factory=dict)
    timeout: int = Field(default=60, gt=0, description="Provider timeout in whole seconds.")


class RecognizePageOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_position: int
    page_position: int
    image_file_id: str
    text_file_id: str
    model_id: str
    config_digest: str
    request_key: str
    method: str
    duration_ms: int = Field(ge=0)


class RecognizePageStepImpl(StepImpl):
    """One admitted WF external request for exactly one text-poor page."""

    key = "recognize_page"
    label = "Recognize one page"
    category = "Activity"
    deterministic = False
    idempotent = False
    effect = StepEffect.EXTERNAL
    execution_mode = StepExecutionMode.EXTERNAL_OPERATION
    effect_description = "Requests one provider recognition and stores a READY text carrier."
    input_model = RecognizePageInput
    output_model = RecognizePageOutput
    outcomes = (StepOutcome("recognized", "Recognized"),)

    @classmethod
    def external_operation_policy(cls, *, attempt: Any) -> ExternalOperationPolicy:
        del attempt
        # Current recognizers do not promise same-key provider replay.
        return ExternalOperationPolicy.UNSUPPORTED

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        del attempt
        return RecoveryCapability(
            RecoveryMode.FRESH, requires_uncertainty_ack=True,
            uncertainty_reason="The provider may have processed this page without a retained result.",
        )

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        return self._recognize(step_run)

    def run_recovery(
        self, step_run: Any, *, now: datetime, source_attempt: Any, mode: RecoveryMode,
    ) -> StepResult:
        del now, source_attempt
        if mode is not RecoveryMode.FRESH:
            raise ValidationError({"recovery": "Page recognition requires fresh acknowledged recovery."})
        return self._recognize(step_run)

    def _recognize(self, step_run: Any) -> StepResult:
        request = external_operation_request(step_run)
        value = self.validate_input(request.input)
        if value.config_digest != canonical_json_sha256(value.engine_config):
            raise ValidationError({"recognition": "The page item names a different admitted recognizer config."})
        actor = step_run.run.admission_actor()
        if actor is None:
            raise PermissionDenied("Page recognition requires the workflow actor.")
        with actor_context(actor):
            file_model = apps.get_model("storage", "File")
            model_model = apps.get_model("agents", "InferenceModel")
            image_file = file_model.objects.get(sqid=value.image_file_id)
            if not image_file.with_actor(actor).has_access("read") or str(image_file.upload_state) != "ready":
                raise PermissionDenied("Read access to the READY page carrier is required.")
            if str(image_file.content_hash) != value.image_digest:
                raise ValidationError({"recognition": "The page carrier digest changed."})
            model = model_model.objects.get(sqid=value.model_id)
            if not model.with_actor(actor).has_access("read"):
                raise PermissionDenied("Read access to the recognition model is required.")
            require_approved_model_deployment(model, role="recognition")
            with image_file.open_stream() as stream:
                image_bytes = stream.read()
            if hashlib.sha256(image_bytes).hexdigest() != value.image_digest:
                raise ValidationError({"recognition": "The stored page image bytes changed."})
            engine_class = resolve_impl_class(
                "ANGEE_EXTRACTION_ENGINE_CLASSES", value.engine, base_class=ExtractionEngine,
            )
            engine = engine_class()
            engine.validate_model(model, role="recognition")
            try:
                response = engine.recognize_page(
                    PageImage(value.source_position, value.page_position, "image/jpeg", image_bytes,
                              value.width, value.height, value.dpi),
                    model=model, config=value.engine_config, timeout=value.timeout,
                )
            except DocumentPipelineError as error:
                step_run.run.debit_budget(error.usage_delta)
                raise
            step_run.run.debit_budget(response.usage_delta)
            if not isinstance(response.text, str) or "\x00" in response.text:
                raise ValidationError({"recognition": "The recognizer did not return valid text."})
            text = response.text.encode("utf-8")
            digest = hashlib.sha256(text).hexdigest()
            facts = {
                "image_file_id": value.image_file_id, "image_digest": value.image_digest,
                "text_digest": digest, "model_id": value.model_id,
                "config_digest": value.config_digest,
                "source_position": value.source_position, "page_position": value.page_position,
            }
            text_file = file_model.objects.ingest_stream(
                ContentFile(text), filename=f"recognized-page-{value.source_position}-{value.page_position}.txt",
                content_hash=digest, size_bytes=len(text), owner_id=actor_user_id(actor),
                drive_id=str(image_file.drive.sqid),
                metadata={"workflows_extraction": {"recognitions": {request.request_key: facts}}},
            )
        return StepResult.done(output={
            "source_position": value.source_position, "page_position": value.page_position,
            "image_file_id": value.image_file_id, "text_file_id": str(text_file.sqid),
            "model_id": value.model_id, "config_digest": value.config_digest,
            "request_key": request.request_key,
            "method": f"{value.engine}:text_recognition", "duration_ms": max(response.duration_ms, 0),
        }, outcome="recognized", artifacts=(ArtifactSpec(text_file, "Recognized page text"),))


class CollectCarriersInput(ExtractionEngineConfigInput):
    model_config = ConfigDict(extra="forbid")
    prepared: dict[str, Any]
    recognition: dict[str, Any]


class CollectCarriersOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prepared: dict[str, Any]
    recognition_results: list[dict[str, Any]]
    hold_reasons: list[str]
    completed_page_count: int


class CollectCarriersStepImpl(StepImpl):
    key = "collect_carriers"
    label = "Collect complete page carriers"
    category = "Activity"
    deterministic = True
    idempotent = True
    effect = StepEffect.READ
    input_model = CollectCarriersInput
    output_model = CollectCarriersOutput
    outcomes = (StepOutcome("collected", "Collected"), StepOutcome("source_hold", "Source hold"))

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        value = self.validate_input(step_run.input)
        options = value.engine_config
        actor = step_run.run.admission_actor()
        if actor is None:
            raise PermissionDenied("Carrier collection requires the workflow actor.")
        with actor_context(actor):
            prepared, manifest = _restore_prepared(value.prepared, options)
            if (
                canonical_json_sha256(dict(options.get("recognition_config") or {}))
                != manifest.recognition_config_digest
            ):
                raise ValidationError({"recognition": "The admitted recognizer configuration changed."})
            results = value.recognition.get("results")
            if not isinstance(results, list):
                raise ValidationError({"recognition": "Map did not retain ordered page results."})
            collected = collect_carriers(
                prepared, results, recognition_model_id=manifest.recognition_model_id,
                recognition_config_digest=manifest.recognition_config_digest,
            )
        return StepResult.done(output={
            "prepared": value.prepared, "recognition_results": results,
            "hold_reasons": list(collected.hold_reasons),
            "completed_page_count": max(len(prepared.pages) - len(collected.hold_reasons), 0),
        }, outcome="source_hold" if collected.hold_reasons else "collected")


class ProcessEvidenceInput(ExtractionPolicyInput, CollectCarriersOutput):
    """Reference-only carrier collection passed from the native Map boundary."""

    identity_mapping: dict[str, str] = Field(default_factory=dict)
    retired_identities: dict[str, str] = Field(default_factory=dict)


class ProcessEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int
    status: str
    error_code: str
    unresolved_reasons: list[str]


class ProcessEvidenceStepImpl(StepImpl):
    key = "process_evidence"
    label = "Retain processed document evidence"
    category = "Activity"
    deterministic = True
    idempotent = True
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    input_model = ProcessEvidenceInput
    output_model = ProcessEvidenceOutput
    outcomes = (StepOutcome("processed", "Processed"), StepOutcome("source_hold", "Source hold"))

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        """Keep evidence retention non-replayable until its write set is reconcilable."""

        del attempt
        return RecoveryCapability(None, "Processed evidence retention has no recovery reconciliation contract.")

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        value = self.validate_input(step_run.input)
        actor = step_run.run.admission_actor()
        if actor is None:
            raise PermissionDenied("Evidence processing requires the workflow actor.")
        with actor_context(actor):
            prepared, manifest = _restore_prepared(value.prepared, value.engine_config)
            collected = collect_carriers(
                prepared, value.recognition_results,
                recognition_model_id=manifest.recognition_model_id,
                recognition_config_digest=manifest.recognition_config_digest,
            )
            if list(collected.hold_reasons) != value.hold_reasons:
                raise ValidationError({"recognition": "The collected source hold changed before retention."})
            target = apps.get_model(manifest.target_model).objects.get(sqid=manifest.target_id)
            model_model = apps.get_model("agents", "InferenceModel")
            mapping_model = (
                model_model.objects.get(sqid=manifest.mapping_model_id)
                if manifest.mapping_model_id else None
            )
            recognition_model = (
                model_model.objects.get(sqid=manifest.recognition_model_id)
                if manifest.recognition_model_id else None
            )
            evidence = process(
                prepared, value.recognition_results, schema=value.schema_, authorized_target=target,
                engine=value.engine, config=value.engine_config,
                model=mapping_model, recognition_model=recognition_model,
                identity_mapping=value.identity_mapping,
                retired_identities=value.retired_identities,
            )
        return StepResult.done(output={
            "extraction_id": str(evidence.sqid), "revision": evidence.revision,
            "status": evidence.status, "error_code": evidence.error_code,
            "unresolved_reasons": list(evidence.unresolved_reasons),
        }, outcome="source_hold" if evidence.status != "succeeded" else "processed",
        artifacts=(ArtifactSpec(evidence, "Document extraction evidence"),))


class InferEvidenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_extraction_id: str
    base_revision: int = Field(ge=1)
    allow_inference: bool = True
    model_id: str | None = None
    target_model: str
    target_id: str
    identity_mapping: dict[str, str] = Field(default_factory=dict)
    retired_identities: dict[str, str] = Field(default_factory=dict)


class InferEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int = Field(ge=1)
    status: str
    error_code: str
    unresolved_reasons: list[str]
    superseded_by: str | None = None
    inference_failure: dict[str, str] | None = None


class InferEvidenceStepImpl(StepImpl):
    key = "infer_evidence"
    label = "Infer unresolved document facts"
    category = "Activity"
    deterministic = False
    idempotent = False
    effect = StepEffect.EXTERNAL
    execution_mode = StepExecutionMode.EXTERNAL_OPERATION
    effect_description = "Requests one bound mapping model response and retains a CAS successor."
    input_model = InferEvidenceInput
    output_model = InferEvidenceOutput
    outcomes = (
        StepOutcome("inferred", "Inferred"), StepOutcome("unchanged", "Unchanged"),
        StepOutcome("inference_failed", "Inference failed; source review required"),
        StepOutcome("source_unavailable", "Original evidence needs review"),
        StepOutcome("correspondence_required", "Correspondence required"),
        StepOutcome("superseded", "Superseded"),
    )

    @classmethod
    def external_operation_policy(cls, *, attempt: Any) -> ExternalOperationPolicy:
        del attempt
        return ExternalOperationPolicy.UNSUPPORTED

    @classmethod
    def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
        del attempt
        return RecoveryCapability(
            RecoveryMode.FRESH, requires_uncertainty_ack=True,
            uncertainty_reason="The mapping provider may have answered without a retained result.",
        )

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        del now
        return self._infer(step_run)

    def run_recovery(
        self, step_run: Any, *, now: datetime, source_attempt: Any, mode: RecoveryMode,
    ) -> StepResult:
        del now, source_attempt
        if mode is not RecoveryMode.FRESH:
            raise ValidationError({"recovery": "Inference requires fresh acknowledged recovery."})
        return self._infer(step_run)

    def _infer(self, step_run: Any) -> StepResult:
        request = external_operation_request(step_run)
        value = self.validate_input(request.input)
        actor = step_run.run.admission_actor()
        if actor is None:
            raise PermissionDenied("Bound inference requires the workflow actor.")
        with actor_context(actor):
            base = apps.get_model("workflows_extraction", "Extraction").objects.get(sqid=value.base_extraction_id)
            if base.revision != value.base_revision:
                raise ValidationError({"base_revision": "The retained extraction revision differs."})
            target = apps.get_model(value.target_model).objects.get(sqid=value.target_id)
            target_ref = canonical_record_target(target)
            if (
                target_ref.content_type.pk != base.content_type_id
                or str(target_ref.object_id) != str(base.object_id)
            ):
                raise ValidationError({"target_id": "The target differs from the retained extraction."})
            if not value.allow_inference:
                if base.status != "succeeded":
                    raise ValidationError({
                        "base_extraction_id": "Retained-only inference requires successful evidence."
                    })
                current = type(base).objects.inference_current_head(base, actor=actor)
                if current.pk != base.pk:
                    if (
                        current.status == "failed"
                        and current.error_code
                        == "source_hold:identity_correspondence_required"
                        and type(base).objects.inference_authority_base(
                            current, actor=actor,
                        ).pk
                        == base.pk
                    ):
                        return _retained_inference_result(
                            current,
                            actor=actor,
                            success_outcome="correspondence_required",
                            artifact_label="Current extraction evidence",
                        )
                    raise ValidationError({
                        "base_extraction_id": (
                            "The current extraction is not a correspondence hold for the retained base."
                        )
                    })
                return StepResult.done(
                    output=_inference_output(base), outcome="unchanged",
                    artifacts=(ArtifactSpec(base, "Retained extraction evidence"),),
                )
            current = type(base).objects.inference_current_head(base, actor=actor)
            if current.pk != base.pk:
                if (
                    current.status == "failed"
                    and current.error_code
                    == "source_hold:identity_correspondence_required"
                ):
                    return _retained_inference_result(
                        current,
                        actor=actor,
                        success_outcome="correspondence_required",
                        artifact_label="Current extraction evidence",
                    )
            else:
                profile = resolve_impl_class(
                    "ANGEE_EXTRACTION_ENGINE_CLASSES",
                    str(base.engine),
                    base_class=ExtractionEngine,
                )()
                unchanged = (
                    base.status == "succeeded"
                    and (
                        bool(base.corrections)
                        or not profile.inference_required(
                            base.result, base.unresolved_reasons
                        )
                        or "mapping" in base.provenance.get("used_model_roles", ())
                    )
                )
                if unchanged:
                    return StepResult.done(
                        output=_inference_output(base), outcome="unchanged",
                        artifacts=(ArtifactSpec(base, "Retained extraction evidence"),),
                    )
            if value.model_id is None:
                raise ValidationError({"model_id": "An admitted mapping model is required."})
            model = apps.get_model("agents", "InferenceModel").objects.get(sqid=value.model_id)
            try:
                outcome = infer(
                    base,
                    model=model,
                    authorized_target=target,
                    operation_step_run=step_run,
                    identity_mapping=value.identity_mapping,
                    retired_identities=value.retired_identities,
                )
            except DocumentPipelineError as error:
                step_run.run.debit_budget(error.usage_delta)
                if (
                    error.code == RETAINED_CARRIER_UNAVAILABLE
                    and error.stage == "correspondence"
                ):
                    authority = type(base).objects.inference_authority_base(
                        base, actor=actor,
                    )
                    if authority.pk != base.pk:
                        return StepResult.done(
                            output={
                                **_inference_output(base),
                                "inference_failure": _inference_failure(error),
                            },
                            outcome="source_unavailable",
                            artifacts=(
                                ArtifactSpec(base, "Current source evidence"),
                                ArtifactSpec(authority, "Original retained evidence"),
                            ),
                        )
                return StepResult.done(
                    output={
                        **_inference_output(base),
                        "inference_failure": _inference_failure(error),
                    },
                    outcome="inference_failed",
                    artifacts=(ArtifactSpec(base, "Source evidence requiring manual review"),),
                )
        step_run.run.debit_budget(outcome.usage_delta)
        if isinstance(outcome, SupersededInference):
            with actor_context(actor):
                current = apps.get_model("workflows_extraction", "Extraction").objects.get(
                    sqid=outcome.current_extraction_id,
                )
            return StepResult.done(
                output={**_inference_output(current), "superseded_by": outcome.current_extraction_id},
                outcome="superseded", artifacts=(ArtifactSpec(current, "Current extraction evidence"),),
            )
        return _retained_inference_result(
            outcome.extraction,
            actor=actor,
            success_outcome="inferred",
            artifact_label="Inferred extraction evidence",
        )


def _inference_failure(error: DocumentPipelineError) -> dict[str, str]:
    """Project one bounded provider or retained-carrier failure into the journal."""

    return {
        "type": type(error).__name__,
        "message": str(error),
        "stage": str(error.stage or ""),
        "code": str(error.code or ""),
    }


def _inference_output(extraction: Any) -> dict[str, Any]:
    return {
        "extraction_id": str(extraction.sqid), "revision": extraction.revision,
        "status": extraction.status, "error_code": extraction.error_code,
        "unresolved_reasons": list(extraction.unresolved_reasons),
    }


def _retained_inference_result(
    extraction: Any,
    *,
    actor: Any,
    success_outcome: str,
    artifact_label: str,
) -> StepResult:
    """Route one exact retained result without inventing correspondence choices."""

    if extraction.status == "failed" and extraction.error_code != (
        "source_hold:identity_correspondence_required"
    ):
        failure = extraction.stage_provenance.get("failure", {})
        return StepResult.done(
            output={
                **_inference_output(extraction),
                "inference_failure": {
                    "type": str(failure.get("type") or "DocumentPipelineError"),
                    "message": str(failure.get("message") or "Inference failed."),
                    "stage": str(failure.get("stage") or ""),
                    "code": str(failure.get("code") or ""),
                },
            },
            outcome="inference_failed",
            artifacts=(ArtifactSpec(extraction, "Failed inferred extraction evidence"),),
        )
    if not (
        extraction.status == "failed"
        and extraction.error_code
        == "source_hold:identity_correspondence_required"
    ):
        return StepResult.done(
            output=_inference_output(extraction),
            outcome=success_outcome,
            artifacts=(ArtifactSpec(extraction, artifact_label),),
        )
    manager = type(extraction).objects
    if manager.inference_candidate_selectors(extraction):
        return StepResult.done(
            output=_inference_output(extraction),
            outcome="correspondence_required",
            artifacts=(ArtifactSpec(extraction, artifact_label),),
        )
    authority = manager.inference_authority_base(extraction, actor=actor)
    return StepResult.done(
        output={
            **_inference_output(authority),
            "inference_failure": {
                "type": "DocumentPipelineError",
                "message": "The retained correspondence candidate is empty.",
                "stage": "correspondence",
                "code": "empty_correspondence_candidate",
            },
        },
        outcome="inference_failed",
        artifacts=(
            ArtifactSpec(extraction, "Empty correspondence candidate"),
            ArtifactSpec(authority, "Source evidence requiring manual review"),
        ),
    )


class ReviseEvidenceInput(BaseModel):
    """Exact retained base, corrected value, and admitted native gate projection."""

    model_config = ConfigDict(extra="forbid")
    base_extraction_id: str
    base_revision: int = Field(ge=1)
    expected_target_id: str
    review: DecisionGateOutput


class GenericEvidenceCorrection(BaseModel):
    """Policy-free changed or explicitly confirmed facts from the exact Decision."""

    model_config = ConfigDict(extra="forbid")
    result: dict[str, Any]
    identity_mapping: dict[str, str] | None = None
    retired_identities: dict[str, str] = Field(default_factory=dict)
    confirmed_paths: list[str] = Field(default_factory=list)


class ReviseEvidenceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_action: str = Field(min_length=1)
    expected_target_model: str = Field(min_length=1)


class ReviseEvidenceStepImpl(DecisionApplyStep):
    """Retain a generic correction from one exact local native Decision gate."""

    key = "revise_evidence"
    label = "Retain corrected evidence"
    category = "Activity"
    deterministic = False
    idempotent = True
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    replay_mode = RecoveryMode.FRESH
    effect_description = "Consumes one exact Decision resolution and clones immutable evidence once."
    input_model = ReviseEvidenceInput
    output_model = ProcessEvidenceOutput
    config_model = ReviseEvidenceConfig
    outcomes = (StepOutcome("revised", "Revised"),)
    resolution_path = ("review", "resolutions", 0)

    def locked_record_basis(
        self,
        step_run: Any,
        predecessor: Any,
        *,
        actor: Any,
    ) -> CorrectionBasis:
        """Lock the exact extraction basis named by the predecessor Decision."""

        del actor
        value = self.validate_input(step_run.input)
        config = ReviseEvidenceConfig.model_validate(step_run.step.config)
        if len(value.review.resolutions) != 1:
            raise ValidationError({"review": "Generic evidence correction requires one resolution."})
        if (
            predecessor.action != config.expected_action
            or predecessor.target_model != config.expected_target_model
            or predecessor.target_id != value.expected_target_id
        ):
            raise ValidationError({"review": "Correction Decision differs from the declared action or target."})
        execution_actor = step_run.run.execution_admission_actor()
        with actor_context(execution_actor):
            extraction = apps.get_model("workflows_extraction", "Extraction").objects.get(
                sqid=value.base_extraction_id,
            )
        if extraction.revision != value.base_revision:
            raise ValidationError({"base_revision": "The retained extraction revision differs."})
        return correction_basis(extraction, decision=predecessor)

    def apply_resolution(
        self,
        step_run: Any,
        decision: Any,
        admitted: Any,
        *,
        actor: Any,
        record_basis: CorrectionBasis,
        now: datetime,
    ) -> StepResult:
        """Retain the correction from the one factory-admitted resolution."""

        del now
        execution_actor = step_run.run.execution_admission_actor()
        correction = GenericEvidenceCorrection.model_validate_json(
            json.dumps(admitted.resolution, allow_nan=False),
        )
        with actor_context(actor):
            corrected = retain_correction_revision(
                record_basis,
                result=correction.result,
                decision=decision,
                revision_owner_id=actor_user_id(to_subject_ref(execution_actor)),
                identity_mapping=correction.identity_mapping,
                retired_identities=correction.retired_identities,
                confirmed_paths=correction.confirmed_paths,
            )
        return StepResult.done(
            output=_inference_output(corrected),
            outcome="revised",
            artifacts=(ArtifactSpec(corrected, "Corrected extraction evidence"),),
        )


def _restore_prepared(
    value: dict[str, Any], options: dict[str, Any],
) -> tuple[Any, PreparePagesOutput]:
    manifest = PreparePagesOutput.model_validate(value)
    sources = manifest.manifest.get("sources")
    if not isinstance(sources, list):
        raise ValidationError({"pages": "The prepared source manifest is invalid."})
    file_ids = [str(item["file"]) for item in sources if isinstance(item, dict) and "file" in item]
    part_ids = [str(item["message_part"]) for item in sources if isinstance(item, dict) and "message_part" in item]
    input_refs = ExtractionSourceInput(
        files=file_ids, message_parts=part_ids, target_model=manifest.target_model,
        target_id=manifest.target_id,
    )
    files, parts, target = _resolve_sources(input_refs)
    prepared = restore_prepared_pages(
        manifest.manifest, files=files, message_parts=parts,
        authorized_target=target, config=options,
    )
    if [
        {"source_position": page.source_position, "page_position": page.page_position,
         "image_file_id": str(page.recognition_file.sqid),
         "image_digest": str(page.recognition_file.content_hash),
         "width": page.recognition_image.width, "height": page.recognition_image.height,
         "dpi": page.recognition_image.dpi, "model_id": manifest.recognition_model_id,
         "config_digest": manifest.recognition_config_digest}
        for page in prepared.recognition_pages
    ] != manifest.recognition_pages:
        raise ValidationError({"pages": "The recognition subset changed after preparation."})
    return prepared, manifest


def _resolve_sources(value: ExtractionSourceInput) -> tuple[list[Any], list[Any], Any]:
    file_model = apps.get_model("storage", "File")
    part_model = apps.get_model("messaging", "Part")
    requested_files = list(file_model.objects.filter(sqid__in=value.files))
    files_by_id = {str(file.sqid): file for file in requested_files}
    if any(file_id not in files_by_id for file_id in value.files):
        raise ValidationError({"files": "One or more source Files are unavailable."})
    requested_parts = list(
        part_model.objects.filter(sqid__in=value.message_parts).select_related("message", "fragment")
    )
    parts_by_id = {str(part.sqid): part for part in requested_parts}
    if any(part_id not in parts_by_id for part_id in value.message_parts):
        raise ValidationError({"message_parts": "One or more Message Parts are unavailable."})
    target = apps.get_model(value.target_model).objects.get(sqid=value.target_id)
    return [files_by_id[item] for item in value.files], [parts_by_id[item] for item in value.message_parts], target
