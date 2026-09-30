"""Typed workflow adapter over the extraction domain."""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any, Literal, cast

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured, ValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from angee.base.impl import resolve_impl_class
from angee.extraction.acquisition import (
    ExtractionConfig,
    ExtractionRequestConfig,
    PageCarrier,
    PartCarrier,
    PreparedDocument,
    RecognitionOutput,
    _text_part,
)
from angee.extraction.contracts import (
    ExtractionPartKind,
    PipelineError,
    Result,
)
from angee.extraction.enums import ExtractionRole, ExtractionStatus
from angee.extraction.inference import RETAINED_CARRIER_UNAVAILABLE, TransientInferenceError, map_parts, recognize_page
from angee.extraction.managers import StaleExtraction
from angee.extraction.profiles import ExtractionProfile, UnconfiguredExtractionProfile
from angee.workflows.maps import MapItem
from angee.workflows.steps import Done, Retryable, RetryPolicy, Step, StepMode


class ProfileConfig(BaseModel):
    """Author-controlled deterministic profile settings."""

    model_config = ConfigDict(extra="forbid")
    profile: str = Field(min_length=1)
    profile_config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def published_profile(self) -> ProfileConfig:
        """Resolve the declared profile and its native typed settings at publish."""
        try:
            selected = resolve_impl_class(ExtractionProfile, self.profile)
            if selected is UnconfiguredExtractionProfile:
                raise ValueError("Select a configured extraction profile.")
            selected.parse_config(self.profile_config)
        except (ValidationError, ImproperlyConfigured) as error:
            raise ValueError(str(error)) from error
        return self


class ExtractionSourceInput(BaseModel):
    """Actor-readable sources and the retained evidence target."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(default_factory=list)
    message_parts: list[str] = Field(default_factory=list)
    target_model: str
    target_id: str


class ProcessEvidenceConfig(ProfileConfig):
    """Published schema and profile used to interpret retained carriers."""

    schema_: dict[str, Any] = Field(alias="schema")

    @field_validator("schema_")
    @classmethod
    def published_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        """Check schema syntax and version before any recognition can run."""
        try:
            ExtractionProfile.check_schema(schema)
        except ValidationError as error:
            raise ValueError(str(error)) from error
        return schema


class PreparePagesOutput(BaseModel):
    """The immutable preparation snapshot and its recognition subset."""

    model_config = ConfigDict(extra="forbid")
    prepared: PreparedDocument
    pages: list[PageCarrier]


class PreparePagesConfig(ProfileConfig):
    """Published profile and bounded acquisition settings."""

    acquisition: ExtractionConfig = Field(default_factory=ExtractionConfig)


class InferenceConfig(BaseModel):
    """Published model request settings."""

    model_config = ConfigDict(extra="forbid")
    request: ExtractionRequestConfig = Field(default_factory=ExtractionRequestConfig)


class _IOStep(Step[None, None, None]):
    """Shared execution bounds for storage and inference IO."""

    mode = StepMode.IO
    retry = RetryPolicy(max_attempts=3)


class PreparePagesStep(_IOStep, Step[ExtractionSourceInput, PreparePagesOutput, PreparePagesConfig]):
    """Prepare each authorized source once before mapping its raster pages."""

    key = "prepare_pages"
    label = "Prepare document pages"
    outcomes = {"prepared": "Prepared"}

    def run(self, ctx: Any) -> Done:
        """Resolve source authority and delegate acquisition to extraction."""
        value = ctx.input
        if not value.files and not value.message_parts:
            raise ValidationError("At least one extraction source is required.")
        if len(set(value.files)) != len(value.files) or len(set(value.message_parts)) != len(value.message_parts):
            raise ValidationError("Extraction sources must be unique.")
        ctx.load(apps.get_model(value.target_model), value.target_id)
        files = [ctx.load(apps.get_model("storage.File"), public_id) for public_id in value.files]
        parts = [ctx.load(apps.get_model("messaging.Part"), public_id) for public_id in value.message_parts]
        profile = (
            apps.get_model("extraction.Extraction").impl_field("profile").resolve_class(ctx.config.profile)()
        )
        profile.parse_config(ctx.config.profile_config)
        prepared = apps.get_model("extraction.Extraction").objects.prepare_pages(
            files, parts, profile=profile, config=ctx.config.acquisition, actor=ctx.actor, heartbeat=ctx.heartbeat,
        )
        return ctx.done(PreparePagesOutput(prepared=prepared, pages=prepared.recognition_pages), outcome="prepared")


class RecognizePageInput(BaseModel):
    """A prepared raster and the model selected to recognize it."""

    model_config = ConfigDict(extra="forbid")
    page: PageCarrier
    model_id: str | None = None


class RecognizePageStep(_IOStep, Step[RecognizePageInput, RecognitionOutput, InferenceConfig]):
    """Recognize exactly one page at the fenced external-effect boundary."""

    key = "recognize_page"
    label = "Recognize one page"
    outcomes = {"recognized": "Recognized"}

    def run(self, ctx: Any) -> Done:
        """Validate access and retain typed text bound to the requested page."""
        value = ctx.input
        file = ctx.load(apps.get_model("storage.File"), value.page.image_file_id)
        model = _model(ctx, value.model_id, ExtractionRole.RECOGNITION)
        ctx.heartbeat()
        ctx.begin_effect()
        try:
            response = recognize_page(value.page, file, model, config=ctx.config.request)
        except TransientInferenceError as error:
            raise Retryable(str(error)) from None
        part = _text_part(
            value.page.source_position,
            value.page.page_position,
            response.text,
            "text_recognition",
            kind=cast(ExtractionPartKind, ExtractionPartKind.RECOGNIZED_TEXT),
            width=value.page.width, height=value.page.height, dpi=value.page.dpi,
            duration_ms=response.duration_ms, metadata=response.provider_metadata,
        )
        ctx.heartbeat()
        carrier = PartCarrier.retain(part, actor=ctx.actor, drive_id=str(file.drive.sqid))
        return ctx.done(RecognitionOutput(page=value.page, part=carrier), outcome="recognized")


class ProcessEvidenceInput(BaseModel):
    """Prepared sources and typed map results for an immutable evidence revision."""

    model_config = ConfigDict(extra="forbid")
    prepared: PreparedDocument
    recognition: list[MapItem[RecognitionOutput]]
    target_model: str
    target_id: str
    model_id: str | None = None
    recognition_model_id: str | None = None
    identity_mapping: dict[str, str] = Field(default_factory=dict)
    retired_identities: dict[str, str] = Field(default_factory=dict)

    def carriers(self) -> tuple[tuple[PartCarrier, ...], list[str]]:
        """Bind recognized carriers to prepared pages, retaining partial source holds."""
        requested = self.prepared.recognition_pages
        if len(self.recognition) > len(requested):
            raise ValidationError("Map returned unrequested page results.")
        parts, holds = list(self.prepared.parts), list(self.prepared.hold_reasons)
        for index, page in enumerate(requested):
            item = self.recognition[index] if index < len(self.recognition) else None
            if item is not None and item.index != index:
                raise ValidationError("Map page results must retain their ordered indices.")
            if item is None or item.outcome != "recognized":
                holds.append(f"recognition_unavailable:{page.source_position}:{page.page_position}")
                continue
            response = item.output
            if (
                response.page != page
                or response.part.source_position != page.source_position
                or response.part.source_page != page.page_position
                or response.part.kind != ExtractionPartKind.RECOGNIZED_TEXT
            ):
                raise ValidationError("Recognition returned a different page carrier.")
            parts.append(response.part)
        return tuple(parts), holds


class ExtractionOutput(BaseModel):
    """Stable identity of the exact retained evidence revision."""

    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int = Field(ge=1)


class SucceededEvidenceOutcome(BaseModel):
    """Bounded workflow view of a successful retained outcome."""

    kind: Literal["succeeded"]
    unresolved_reasons: list[str]


class FailedEvidenceOutcome(BaseModel):
    """Bounded workflow view of a failed retained outcome."""

    kind: Literal["failed"]
    code: str
    stage: str | None = None
    unresolved_reasons: list[str]


EvidenceOutcomeSummary = Annotated[
    SucceededEvidenceOutcome | FailedEvidenceOutcome, Field(discriminator="kind")
]


class ProcessEvidenceOutput(ExtractionOutput):
    """Reference and bounded outcome facts exposed to calling workflows."""

    outcome: EvidenceOutcomeSummary

    @classmethod
    def from_extraction(cls, extraction: Any) -> ProcessEvidenceOutput:
        """Project only the retained row's public identity and processing outcome."""
        retained = extraction.outcome
        if retained["kind"] == ExtractionStatus.FAILED:
            summary: SucceededEvidenceOutcome | FailedEvidenceOutcome = FailedEvidenceOutcome(
                kind="failed", code=retained["code"], stage=retained.get("stage"),
                unresolved_reasons=retained.get("unresolved_reasons", []),
            )
        else:
            summary = SucceededEvidenceOutcome(
                kind="succeeded", unresolved_reasons=retained.get("unresolved_reasons", []),
            )
        return cls(
            extraction_id=str(extraction.sqid),
            revision=extraction.revision,
            outcome=summary,
        )


class ProcessEvidenceStep(_IOStep, Step[ProcessEvidenceInput, ProcessEvidenceOutput, ProcessEvidenceConfig]):
    """Interpret carriers through their profile and retain one immutable revision."""

    key = "process_evidence"
    label = "Retain processed evidence"
    outcomes = {"processed": "Processed", "source_hold": "Source hold"}

    def run(self, ctx: Any) -> Done:
        """Recheck authority, apply the profile and delegate retention atomically."""
        value = ctx.input
        model = apps.get_model("extraction.Extraction")
        target = ctx.load(apps.get_model(value.target_model), value.target_id)
        sources = [source.restore(ctx, i) for i, source in enumerate(value.prepared.sources)]
        carriers, holds = value.carriers()
        restored_parts = []
        for carrier in carriers:
            ctx.heartbeat()
            restored_parts.append(carrier.restore(ctx))
        parts = tuple(restored_parts)
        profile = model.impl_field("profile").resolve_class(ctx.config.profile)()
        profile_config = profile.normalize_config(ctx.config.profile_config)
        failure = None
        try:
            result = (
                Result({}, parts, {})
                if holds and not parts
                else profile.process_parts(
                    sources,
                    parts,
                    ctx.config.schema_,
                    config=profile_config,
                    recognition_used=any(part.kind == ExtractionPartKind.RECOGNIZED_TEXT for part in parts),
                )
            )
        except PipelineError as error:
            failure = error
            result = Result({}, parts, {}, provider_metadata={"unresolved_reasons": [error.code]})
        if result.parts != parts:
            raise ValidationError("The selected profile changed the prepared evidence carriers.")
        if holds:
            result = replace(
                result, provider_metadata={**(result.provider_metadata or {}), "unresolved_reasons": holds}
            )
        evidence = model.objects.retain_result(
            sources=sources,
            pages=value.prepared.pages,
            part_carriers=carriers,
            result=result,
            target=target,
            actor=ctx.actor,
            profile=ctx.config.profile,
            profile_config=profile_config,
            schema=ctx.config.schema_,
            model=_model(ctx, value.model_id, ExtractionRole.MAPPING),
            recognition_model=_model(ctx, value.recognition_model_id, ExtractionRole.RECOGNITION),
            request_key=ctx.idempotency_key,
            failure=failure,
            error_code="source_hold" if holds else None,
            identity_mapping=value.identity_mapping,
            retired_identities=value.retired_identities,
        )
        ctx.artifact(evidence, "Document extraction evidence")
        return ctx.done(
            ProcessEvidenceOutput.from_extraction(evidence),
            outcome="processed" if evidence.outcome["kind"] == ExtractionStatus.SUCCEEDED else "source_hold",
        )


class InferEvidenceInput(BaseModel):
    """Exact evidence base and target for a compare-and-swap inference successor."""

    model_config = ConfigDict(extra="forbid")
    base_extraction_id: str
    base_revision: int = Field(ge=1)
    allow_inference: bool = True
    model_id: str | None = None
    target_model: str
    target_id: str
    identity_mapping: dict[str, str] = Field(default_factory=dict)
    retired_identities: dict[str, str] = Field(default_factory=dict)


class InferEvidenceOutput(ProcessEvidenceOutput):
    """Retained successor or current authority with bounded inference diagnostics."""

    superseded_by: str | None = None
    inference_failure: dict[str, str] | None = None


class InferEvidenceStep(_IOStep, Step[InferEvidenceInput, InferEvidenceOutput, InferenceConfig]):
    """Infer unresolved evidence while retaining failures and detecting stale bases."""

    key = "infer_evidence"
    label = "Infer unresolved evidence"
    outcomes = {
        "inferred": "Inferred",
        "unchanged": "Unchanged",
        "inference_failed": "Inference failed",
        "source_unavailable": "Source unavailable",
        "correspondence_required": "Correspondence required",
        "superseded": "Superseded",
    }

    def run(self, ctx: Any) -> Done:
        """Authorize the base, call its model and retain or report the current head."""
        value = ctx.input
        model = apps.get_model("extraction.Extraction")
        base = ctx.load(model, value.base_extraction_id)
        if base.revision != value.base_revision:
            raise ValidationError("The inference base revision changed.")
        target = ctx.load(apps.get_model(value.target_model), value.target_id)
        base.require_target(target)
        reused = model.objects.reused_inference(base, ctx.idempotency_key, actor=ctx.actor)
        if reused is not None:
            outcome = "correspondence_required" if reused.awaiting_correspondence else (
                "inference_failed" if reused.outcome["kind"] == ExtractionStatus.FAILED else "inferred"
            )
            return ctx.done(
                InferEvidenceOutput(
                    **ProcessEvidenceOutput.from_extraction(reused).model_dump(),
                    inference_failure={"stage": reused.outcome["stage"], "code": reused.outcome["code"]}
                    if reused.outcome.get("stage") else None,
                ),
                outcome=outcome,
            )
        current = model.objects.inference_current_head(base, actor=ctx.actor)
        if current.pk != base.pk:
            return ctx.done(
                InferEvidenceOutput(
                    **ProcessEvidenceOutput.from_extraction(current).model_dump(), superseded_by=str(current.sqid)
                ),
                outcome="superseded",
            )
        profile = base.resolve_impl("profile")()
        output = ProcessEvidenceOutput.from_extraction(base).model_dump()
        if base.awaiting_correspondence and not value.identity_mapping:
            return ctx.done(InferEvidenceOutput(**output), outcome="correspondence_required")
        if not base.awaiting_correspondence and (
            not value.allow_inference or not profile.inference_required(base.result, base.unresolved_reasons)
        ):
            return ctx.done(InferEvidenceOutput(**output), outcome="unchanged")
        sources = type(base).objects.authorized_document_sources(base, actor=ctx.actor)
        parts = base.document_parts()
        if base.outcome.get("code") == "source_hold" or not parts:
            return ctx.done(
                InferEvidenceOutput(
                    **output, inference_failure={"stage": "acquisition", "code": RETAINED_CARRIER_UNAVAILABLE}
                ),
                outcome="source_unavailable",
            )
        model.objects.inference_authority_base(base, actor=ctx.actor)
        model_id = str(base.model.sqid) if base.model_id else ""
        inference_model = _model(
            ctx, model_id if base.awaiting_correspondence else value.model_id or model_id, ExtractionRole.MAPPING
        )
        recognition_model = _model(
            ctx, str(base.recognition_model.sqid) if base.recognition_model_id else "", ExtractionRole.RECOGNITION
        )
        failure = None
        if base.awaiting_correspondence:
            result = Result(
                base.result,
                parts,
                dict(base.claims),
                base.used_model_roles,
                provider_metadata=dict(base.stage_provenance),
            )
        else:
            try:
                ctx.heartbeat()
                ctx.begin_effect()
                mapped = map_parts(parts, base.schema, inference_model, config=ctx.config.request)
                result = profile.normalize_inference_candidate(
                    sources,
                    parts,
                    base.schema,
                    value=mapped.value,
                    claims=mapped.claims,
                    metadata=mapped.provider_metadata,
                    config=base.profile_config,
                    recognition_used=ExtractionRole.RECOGNITION in base.used_model_roles,
                )
            except TransientInferenceError as error:
                raise Retryable(str(error)) from None
            except PipelineError as error:
                failure = error
                result = Result(base.result, parts, dict(base.claims), provider_metadata=dict(base.stage_provenance))
            result = replace(
                result,
                used_model_roles=tuple(
                    sorted({*base.used_model_roles, *result.used_model_roles, ExtractionRole.MAPPING})
                ),
            )
        try:
            evidence = model.objects.retain_result(
                sources=(),
                result=result,
                target=target,
                actor=ctx.actor,
                profile=str(base.profile),
                profile_config=base.profile_config,
                schema=base.schema,
                model=inference_model,
                recognition_model=recognition_model,
                request_key=ctx.idempotency_key,
                base=base,
                failure=failure,
                identity_mapping=value.identity_mapping,
                retired_identities=value.retired_identities,
            )
        except StaleExtraction:
            current = model.objects.inference_current_head(base, actor=ctx.actor)
            return ctx.done(
                InferEvidenceOutput(
                    **ProcessEvidenceOutput.from_extraction(current).model_dump(), superseded_by=str(current.sqid)
                ),
                outcome="superseded",
            )
        ctx.artifact(evidence, "Inferred extraction evidence")
        outcome = "correspondence_required" if evidence.awaiting_correspondence else "inferred"
        if evidence.outcome["kind"] != ExtractionStatus.SUCCEEDED and not evidence.awaiting_correspondence:
            outcome = "inference_failed"
        return ctx.done(
            InferEvidenceOutput(
                **ProcessEvidenceOutput.from_extraction(evidence).model_dump(),
                inference_failure={"stage": failure.stage, "code": failure.code} if failure else None,
            ),
            outcome=outcome,
        )


def _model(ctx: Any, public_id: str | None, role: ExtractionRole) -> Any:
    if not public_id:
        return None
    model = ctx.load(apps.get_model("agents.InferenceModel"), public_id)
    model.require_usable(ctx.actor, role, uses=role.accepted_model_uses)
    return model
