"""Typed workflow step that journals only an extraction reference."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Annotated, Any

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from pydantic import BaseModel, ConfigDict, Field
from rebac import actor_context

from angee.base.actors import actor_user_id
from angee.base.db import get_write_alias, related_on
from angee.base.impl import resolve_impl_class
from angee.base.refs import canonical_record_target
from angee.base.serialization import canonical_json_sha256
from angee.workflows.attempts import (
    ArtifactSpec,
    ExternalOperationPolicy,
    RecoveryCapability,
    RecoveryMode,
)
from angee.workflows.engine import external_operation_request
from angee.workflows.steps import (
    StepEffect,
    StepExecutionMode,
    StepImpl,
    StepOutcome,
    StepResult,
)
from angee.workflows_extraction.contracts import DocumentPipelineError, PageImage
from angee.workflows_extraction.inference import (
    RETAINED_CARRIER_UNAVAILABLE,
    recognize_page,
)
from angee.workflows_extraction.profiles import ExtractionProfile
from angee.workflows_extraction.service import (
    SupersededInference,
    collect_carriers,
    infer,
    prepare_pages,
    process,
    restore_prepared_pages,
)

ProfileConfig = Annotated[dict[str, Any], Field(json_schema_extra={"widget": "json"})]


class ExtractionConfigInput(BaseModel):
    """Per-invocation document profile and inference configuration."""

    model_config = ConfigDict(extra="forbid")
    profile_config: ProfileConfig = Field(default_factory=dict)


class ExtractionPolicyInput(ExtractionConfigInput):
    """Per-invocation schema and profile policy for evidence processing."""

    schema_: dict[str, Any] = Field(alias="schema", json_schema_extra={"widget": "json"})
    profile: str = Field(min_length=1)


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


class PreparePagesInput(ExtractionConfigInput, ExtractionSourceInput):
    """The original source/target refs; provider work happens later."""

    profile: str = Field(default="none", min_length=1)
    model: str | None = None
    recognition_model: str | None = None


class RecognitionPageInput(BaseModel):
    """Retained page carrier and frozen policy passed through the Map boundary."""

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


class PreparePagesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = Field(default="none", min_length=1)
    manifest: dict[str, Any]
    recognition_pages: list[RecognitionPageInput]
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
        alias = get_write_alias(type(step_run), instance=step_run)
        run: Any = related_on(step_run, "run", using=alias)
        del now
        value = self.validate_input(step_run.input)
        options = value.profile_config
        actor = run.admission_actor(using=alias)
        if actor is None:
            raise PermissionDenied("Page preparation requires the workflow actor.")
        with actor_context(actor):
            files, parts, target = _resolve_sources(value, using=alias)
            prepared = prepare_pages(
                files=files, message_parts=parts, authorized_target=target, config=options,
                profile=resolve_impl_class(
                    "ANGEE_EXTRACTION_PROFILE_CLASSES", value.profile, base_class=ExtractionProfile,
                )(),
                using=alias,
            )
        model_id = value.recognition_model or ""
        recognition_options = dict(options.get("recognition_config") or {})
        config_digest = canonical_json_sha256(recognition_options)
        recognition_pages = [
            page.recognition_input(model_id=model_id, config_digest=config_digest)
            for page in prepared.recognition_pages
        ]
        return StepResult.done(output={
            "profile": value.profile,
            "manifest": prepared.manifest,
            "recognition_pages": recognition_pages,
            "recognition_model_id": model_id,
            "mapping_model_id": value.model or "",
            "recognition_config_digest": config_digest,
            "target_model": value.target_model,
            "target_id": value.target_id,
        }, outcome="prepared")


class RecognizePageInput(RecognitionPageInput):
    profile_config: ProfileConfig = Field(default_factory=dict)
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
        alias = get_write_alias(type(step_run), instance=step_run)
        del now
        return self._recognize(step_run, using=alias)

    def run_recovery(
        self, step_run: Any, *, now: datetime, source_attempt: Any, mode: RecoveryMode,
    ) -> StepResult:
        alias = get_write_alias(type(step_run), instance=step_run)
        del now, source_attempt
        if mode is not RecoveryMode.FRESH:
            raise ValidationError({"recovery": "Page recognition requires fresh acknowledged recovery."})
        return self._recognize(step_run, using=alias)

    def _recognize(self, step_run: Any, *, using: str) -> StepResult:
        run: Any = related_on(step_run, "run", using=using)
        request = external_operation_request(step_run, using=using)
        value = self.validate_input(request.input)
        if value.config_digest != canonical_json_sha256(value.profile_config):
            raise ValidationError({"recognition": "The page item names a different admitted recognizer config."})
        actor = run.admission_actor(using=using)
        if actor is None:
            raise PermissionDenied("Page recognition requires the workflow actor.")
        actor_subject = run.admission_actor_subject()
        if actor_subject is None:
            raise PermissionDenied("Page recognition requires the workflow actor subject.")
        with actor_context(actor):
            file_model = apps.get_model("storage", "File")
            model_model = apps.get_model("agents", "InferenceModel")
            image_file = file_model.objects.db_manager(using).select_related("drive").get(sqid=value.image_file_id)
            if not image_file.with_actor(actor).has_access("read") or str(image_file.upload_state) != "ready":
                raise PermissionDenied("Read access to the READY page carrier is required.")
            if str(image_file.content_hash) != value.image_digest:
                raise ValidationError({"recognition": "The page carrier digest changed."})
            model = model_model.objects.db_manager(using).get(sqid=value.model_id)
            with image_file.open_stream() as stream:
                image_bytes = stream.read()
            if hashlib.sha256(image_bytes).hexdigest() != value.image_digest:
                raise ValidationError({"recognition": "The stored page image bytes changed."})
            response = recognize_page(
                PageImage(
                    value.source_position,
                    value.page_position,
                    "image/jpeg",
                    image_bytes,
                    value.width,
                    value.height,
                    value.dpi,
                ),
                step_run=step_run,
                model=model,
                config=value.profile_config,
                timeout=value.timeout,
                using=using,
            )
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
            text_file = file_model.objects.db_manager(using).ingest_stream(
                ContentFile(text), filename=f"recognized-page-{value.source_position}-{value.page_position}.txt",
                content_hash=digest, size_bytes=len(text), owner_id=actor_user_id(actor_subject),
                drive_id=str(image_file.drive.sqid),
                metadata={"workflows_extraction": {"recognitions": {request.request_key: facts}}},
            )
        return StepResult.done(
            output={
                "source_position": value.source_position,
                "page_position": value.page_position,
                "image_file_id": value.image_file_id,
                "text_file_id": str(text_file.sqid),
                "model_id": value.model_id,
                "config_digest": value.config_digest,
                "request_key": request.request_key,
                "method": "inference:text_recognition",
                "duration_ms": max(response.duration_ms, 0),
            },
            outcome="recognized",
            artifacts=(ArtifactSpec(text_file, "Recognized page text"),),
        )


class CollectCarriersInput(ExtractionConfigInput):
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
        alias = get_write_alias(type(step_run), instance=step_run)
        run: Any = related_on(step_run, "run", using=alias)
        del now
        value = self.validate_input(step_run.input)
        options = value.profile_config
        actor = run.admission_actor(using=alias)
        if actor is None:
            raise PermissionDenied("Carrier collection requires the workflow actor.")
        with actor_context(actor):
            prepared, manifest = _restore_prepared(value.prepared, options, using=alias)
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
                using=alias,
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
        alias = get_write_alias(type(step_run), instance=step_run)
        run: Any = related_on(step_run, "run", using=alias)
        del now
        value = self.validate_input(step_run.input)
        actor = run.admission_actor(using=alias)
        if actor is None:
            raise PermissionDenied("Evidence processing requires the workflow actor.")
        with actor_context(actor):
            prepared, manifest = _restore_prepared(value.prepared, value.profile_config, using=alias)
            if manifest.profile != value.profile:
                raise ValidationError({"profile": "The selected profile differs from the prepared profile."})
            collected = collect_carriers(
                prepared, value.recognition_results,
                recognition_model_id=manifest.recognition_model_id,
                recognition_config_digest=manifest.recognition_config_digest,
                using=alias,
            )
            if list(collected.hold_reasons) != value.hold_reasons:
                raise ValidationError({"recognition": "The collected source hold changed before retention."})
            target = apps.get_model(manifest.target_model).objects.db_manager(alias).get(sqid=manifest.target_id)
            model_model = apps.get_model("agents", "InferenceModel")
            mapping_model = (
                model_model.objects.db_manager(alias).get(sqid=manifest.mapping_model_id)
                if manifest.mapping_model_id else None
            )
            recognition_model = (
                model_model.objects.db_manager(alias).get(sqid=manifest.recognition_model_id)
                if manifest.recognition_model_id else None
            )
            evidence = process(
                prepared, value.recognition_results, schema=value.schema_, authorized_target=target,
                profile=value.profile, config=value.profile_config,
                model=mapping_model, recognition_model=recognition_model,
                identity_mapping=value.identity_mapping,
                retired_identities=value.retired_identities,
                using=alias,
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
        alias = get_write_alias(type(step_run), instance=step_run)
        del now
        return self._infer(step_run, using=alias)

    def run_recovery(
        self, step_run: Any, *, now: datetime, source_attempt: Any, mode: RecoveryMode,
    ) -> StepResult:
        alias = get_write_alias(type(step_run), instance=step_run)
        del now, source_attempt
        if mode is not RecoveryMode.FRESH:
            raise ValidationError({"recovery": "Inference requires fresh acknowledged recovery."})
        return self._infer(step_run, using=alias)

    def _infer(self, step_run: Any, *, using: str) -> StepResult:
        run: Any = related_on(step_run, "run", using=using)
        request = external_operation_request(step_run, using=using)
        value = self.validate_input(request.input)
        actor = run.admission_actor(using=using)
        if actor is None:
            raise PermissionDenied("Bound inference requires the workflow actor.")
        with actor_context(actor):
            base = (
                apps.get_model("workflows_extraction", "Extraction")
                .objects.db_manager(using)
                .get(sqid=value.base_extraction_id)
            )
            if base.revision != value.base_revision:
                raise ValidationError({"base_revision": "The retained extraction revision differs."})
            target = apps.get_model(value.target_model).objects.db_manager(using).get(sqid=value.target_id)
            target_ref = canonical_record_target(target, using=using)
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
                current = type(base).objects.db_manager(using).inference_current_head(base, actor=actor)
                if current.pk != base.pk:
                    if (
                        current.awaiting_correspondence
                        and type(base)
                        .objects.db_manager(using)
                        .inference_authority_base(
                            current,
                            actor=actor,
                        )
                        .pk
                        == base.pk
                    ):
                        return _retained_inference_result(
                            current,
                            actor=actor,
                            success_outcome="correspondence_required",
                            artifact_label="Current extraction evidence",
                            using=using,
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
            current = type(base).objects.db_manager(using).inference_current_head(base, actor=actor)
            if current.pk != base.pk:
                if current.awaiting_correspondence:
                    return _retained_inference_result(
                        current,
                        actor=actor,
                        success_outcome="correspondence_required",
                        artifact_label="Current extraction evidence",
                        using=using,
                    )
            else:
                profile = resolve_impl_class(
                    "ANGEE_EXTRACTION_PROFILE_CLASSES",
                    str(base.profile),
                    base_class=ExtractionProfile,
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
            model = apps.get_model("agents", "InferenceModel").objects.db_manager(using).get(sqid=value.model_id)
            try:
                outcome = infer(
                    base,
                    model=model,
                    authorized_target=target,
                    operation_step_run=step_run,
                    identity_mapping=value.identity_mapping,
                    retired_identities=value.retired_identities,
                    using=using,
                )
            except DocumentPipelineError as error:
                if (
                    error.code == RETAINED_CARRIER_UNAVAILABLE
                    and error.stage == "correspondence"
                ):
                    authority = type(base).objects.db_manager(using).inference_authority_base(
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
        if isinstance(outcome, SupersededInference):
            with actor_context(actor):
                current = apps.get_model("workflows_extraction", "Extraction").objects.db_manager(using).get(
                    sqid=outcome.current_extraction_id,
                )
            if current.status == "failed":
                retained = _retained_inference_result(
                    current,
                    actor=actor,
                    success_outcome="superseded",
                    artifact_label="Current extraction evidence",
                    using=using,
                )
                return StepResult.done(
                    output={
                        **retained.output,
                        "superseded_by": outcome.current_extraction_id,
                    },
                    outcome=retained.outcome,
                    artifacts=retained.artifacts,
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
            using=using,
        )


def _inference_failure(source: DocumentPipelineError | Mapping[str, Any]) -> dict[str, str]:
    """Project a live error or persisted stage provenance into bounded journal facts."""

    if isinstance(source, DocumentPipelineError):
        details: Mapping[str, Any] = {
            "type": type(source).__name__,
            "stage": source.stage,
            "code": source.code,
        }
        message = str(source)
        metadata = source.metadata
    else:
        details = source.get("failure", {})
        message = str(details.get("message") or "Inference failed.")
        # Processing stores diagnostics beside failure; retained inference stores
        # them inside failure.metadata, which takes precedence over inherited facts.
        metadata = details.get("metadata", source)

    failure = {
        "type": str(details.get("type") or "DocumentPipelineError"),
        "message": message,
        "stage": str(details.get("stage") or ""),
        "code": str(details.get("code") or ""),
    }
    for key in ("provider_response_id", "finish_reason", "output_text_length", "output_text_sha256"):
        if key in metadata:
            failure[key] = str(metadata[key])
    return failure


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
    using: str,
) -> StepResult:
    """Route one exact retained result without inventing correspondence choices."""

    if extraction.status == "failed" and not extraction.awaiting_correspondence:
        return StepResult.done(
            output={
                **_inference_output(extraction),
                "inference_failure": _inference_failure(extraction.stage_provenance),
            },
            outcome="inference_failed",
            artifacts=(ArtifactSpec(extraction, "Failed inferred extraction evidence"),),
        )
    if not extraction.awaiting_correspondence:
        return StepResult.done(
            output=_inference_output(extraction),
            outcome=success_outcome,
            artifacts=(ArtifactSpec(extraction, artifact_label),),
        )
    manager = type(extraction).objects.db_manager(using)
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
            "inference_failure": _inference_failure(DocumentPipelineError(
                "The retained correspondence candidate is empty.",
                stage="correspondence",
                code="empty_correspondence_candidate",
            )),
        },
        outcome="inference_failed",
        artifacts=(
            ArtifactSpec(extraction, "Empty correspondence candidate"),
            ArtifactSpec(authority, "Source evidence requiring manual review"),
        ),
    )


def _restore_prepared(
    value: dict[str, Any], options: dict[str, Any],
    *, using: str,
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
    files, parts, target = _resolve_sources(input_refs, using=using)
    prepared = restore_prepared_pages(
        manifest.manifest, files=files, message_parts=parts,
        authorized_target=target, config=options,
        profile=resolve_impl_class(
            "ANGEE_EXTRACTION_PROFILE_CLASSES", manifest.profile, base_class=ExtractionProfile,
        )(),
        using=using,
    )
    if [
        RecognitionPageInput.model_validate(
            page.recognition_input(
                model_id=manifest.recognition_model_id,
                config_digest=manifest.recognition_config_digest,
            )
        )
        for page in prepared.recognition_pages
    ] != manifest.recognition_pages:
        raise ValidationError({"pages": "The recognition subset changed after preparation."})
    return prepared, manifest


def _resolve_sources(value: ExtractionSourceInput, *, using: str) -> tuple[list[Any], list[Any], Any]:
    file_model = apps.get_model("storage", "File")
    part_model = apps.get_model("messaging", "Part")
    requested_files = list(
        file_model.objects.db_manager(using).select_related("mime_type", "drive").filter(sqid__in=value.files)
    )
    files_by_id = {str(file.sqid): file for file in requested_files}
    if any(file_id not in files_by_id for file_id in value.files):
        raise ValidationError({"files": "One or more source Files are unavailable."})
    requested_parts = list(
        part_model.objects.db_manager(using).filter(sqid__in=value.message_parts).select_related("message", "fragment")
    )
    parts_by_id = {str(part.sqid): part for part in requested_parts}
    if any(part_id not in parts_by_id for part_id in value.message_parts):
        raise ValidationError({"message_parts": "One or more Message Parts are unavailable."})
    target = apps.get_model(value.target_model).objects.db_manager(using).get(sqid=value.target_id)
    return [files_by_id[item] for item in value.files], [parts_by_id[item] for item in value.message_parts], target
