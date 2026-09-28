"""Typed workflow dispatch over retained evidence and registry-selected providers."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field

from angee.base.impl import resolve_impl_class
from angee.workflows.steps import Done, Step
from angee.workflows_extraction.contracts import (
    DocumentPart,
    ExtractionPartKind,
    PageImage,
    PipelineError,
    Result,
)
from angee.workflows_extraction.enums import ExtractionRole
from angee.workflows_extraction.inference import RETAINED_CARRIER_UNAVAILABLE
from angee.workflows_extraction.managers import StaleExtraction
from angee.workflows_extraction.providers import ExtractionProvider, PageCarrier, PreparedDocument, RecognitionOutput


class ExtractionConfigInput(BaseModel):
    """Authored deterministic profile and inference settings."""

    model_config = ConfigDict(extra="forbid")
    profile_config: dict[str, Any] = Field(default_factory=dict)


class ExtractionSourceInput(BaseModel):
    """Actor-readable sources and the retained evidence target."""

    model_config = ConfigDict(extra="forbid")
    files: list[str] = Field(default_factory=list)
    message_parts: list[str] = Field(default_factory=list)
    target_model: str
    target_id: str


class ExtractionPolicyInput(ExtractionConfigInput):
    """Published schema and profile used to interpret retained carriers."""

    schema_: dict[str, Any] = Field(alias="schema")
    profile: str = Field(min_length=1)


class PreparePagesInput(ExtractionConfigInput, ExtractionSourceInput):
    """Source references and the profile selected for one preparation."""

    profile: str = Field(default="none", min_length=1)
    model: str | None = None
    recognition_model: str | None = None


class PreparePagesOutput(BaseModel):
    """The immutable preparation snapshot and its recognition subset."""

    model_config = ConfigDict(extra="forbid")
    prepared: PreparedDocument
    pages: list[PageCarrier]


class ProviderConfig(BaseModel):
    """Trusted provider implementation selected by the workflow definition."""

    model_config = ConfigDict(extra="forbid")
    provider: str = "native"


class _ProviderStep:
    """One temporary L2 boundary shared by all steps that can perform I/O."""

    mode = "DATABASE"  # L2: set to "IO" when the engine admits IO mode.
    timeout = timedelta(minutes=5)
    effect_idempotent = False  # L2: these providers make no deduplication guarantee.

    def provider(self, ctx: Any) -> ExtractionProvider:
        """Resolve the trusted adapter and fence its external-effect boundary."""
        provider = resolve_impl_class("ANGEE_EXTRACTION_PROVIDER_CLASSES", ctx.config.provider, ExtractionProvider)()
        if provider.external_io:
            if ctx.step.mode != "IO":
                raise ValidationError("External extraction providers require L2 IO mode.")
            ctx.begin_effect()  # L2: the engine must fence the effect before any provider call.
            ctx.heartbeat()  # L2: the IO attempt owns its deadline.
        return provider


class PreparePagesStep(_ProviderStep, Step[PreparePagesInput, PreparePagesOutput, ProviderConfig]):
    """Prepare each authorized source once before mapping its raster pages."""

    key = "prepare_pages"
    label = "Prepare document pages"
    outcomes = {"prepared": "Prepared"}

    def run(self, ctx: Any) -> Done:
        """Resolve source authority and delegate acquisition to the provider."""
        value = ctx.input
        if not value.files and not value.message_parts:
            raise ValidationError("At least one extraction source is required.")
        if len(set(value.files)) != len(value.files) or len(set(value.message_parts)) != len(value.message_parts):
            raise ValidationError("Extraction sources must be unique.")
        ctx.load(apps.get_model(value.target_model), value.target_id)
        files = [ctx.load(apps.get_model("storage.File"), public_id) for public_id in value.files]
        parts = [ctx.load(apps.get_model("messaging.Part"), public_id) for public_id in value.message_parts]
        profile = apps.get_model("workflows_extraction.Extraction").impl_field("profile").resolve_class(value.profile)()
        prepared = self.provider(ctx).prepare(
            files, parts, profile=profile, config=value.profile_config, actor=ctx.actor
        )
        return ctx.done(PreparePagesOutput(prepared=prepared, pages=prepared.recognition_pages), outcome="prepared")


class RecognizePageInput(ExtractionConfigInput):
    """A prepared raster and the model selected to recognize it."""

    page: PageCarrier
    model_id: str | None = None


class RecognizePageStep(_ProviderStep, Step[RecognizePageInput, RecognitionOutput, ProviderConfig]):
    """Recognize exactly one page at the fenced external-effect boundary."""

    key = "recognize_page"
    label = "Recognize one page"
    outcomes = {"recognized": "Recognized"}

    def run(self, ctx: Any) -> Done:
        """Validate access and retain typed text bound to the requested page."""
        value = ctx.input
        file = ctx.load(apps.get_model("storage.File"), value.page.image_file_id)
        model = _model(ctx, value.model_id, ExtractionRole.RECOGNITION)
        response = self.provider(ctx).recognize(value.page, file, model, config=value.profile_config)
        if "\x00" in response.text:
            raise ValidationError("Recognition text contains null characters.")
        part = DocumentPart(
            value.page.source_position,
            value.page.page_position,
            "text/plain",
            ExtractionPartKind.RECOGNIZED_TEXT,
            response.text,
            "text_recognition",
            hashlib.sha256(response.text.encode()).hexdigest(),
            value.page.width,
            value.page.height,
            value.page.dpi,
            response.duration_ms,
            response.provider_metadata,
        )
        return ctx.done(RecognitionOutput(page=value.page, part=part), outcome="recognized")


class CollectCarriersInput(BaseModel):
    """Preparation plus the map's ordered success and failure records."""

    model_config = ConfigDict(extra="forbid")
    prepared: PreparedDocument
    recognition: list[dict[str, Any]]


class CollectCarriersOutput(BaseModel):
    """Validated carriers with explicit reasons a source requires review."""

    model_config = ConfigDict(extra="forbid")
    prepared: PreparedDocument
    recognition: list[dict[str, Any]]
    hold_reasons: list[str]


class CollectCarriersStep(Step[CollectCarriersInput, CollectCarriersOutput, None]):
    """Validate map completeness without repeating source acquisition."""

    key = "collect_carriers"
    label = "Collect page carriers"
    outcomes = {"collected": "Collected", "source_hold": "Source hold"}

    def run(self, ctx: Any) -> Done:
        """Report complete carriers or an explicit retained source hold."""
        value = ctx.input
        _, holds = value.prepared.collect(value.recognition)
        return ctx.done(
            CollectCarriersOutput(prepared=value.prepared, recognition=value.recognition, hold_reasons=holds),
            outcome="source_hold" if holds else "collected",
        )


class ProcessEvidenceInput(ExtractionPolicyInput, CollectCarriersOutput):
    """Validated carriers and policy for an immutable evidence revision."""

    target_model: str
    target_id: str
    model_id: str | None = None
    recognition_model_id: str | None = None
    identity_mapping: dict[str, str] = Field(default_factory=dict)
    retired_identities: dict[str, str] = Field(default_factory=dict)


class ExtractionOutput(BaseModel):
    """Stable identity of the exact retained evidence revision."""

    model_config = ConfigDict(extra="forbid")
    extraction_id: str
    revision: int


class ProcessEvidenceOutput(ExtractionOutput):
    """Reference and bounded status facts exposed to calling workflows."""

    status: str
    error_code: str
    unresolved_reasons: list[str]

    @classmethod
    def from_extraction(cls, extraction: Any) -> ProcessEvidenceOutput:
        """Project only the retained row's public identity and processing outcome."""
        return cls(
            extraction_id=str(extraction.sqid),
            revision=extraction.revision,
            status=extraction.status,
            error_code=extraction.error_code,
            unresolved_reasons=extraction.unresolved_reasons,
        )


class ProcessEvidenceStep(Step[ProcessEvidenceInput, ProcessEvidenceOutput, None]):
    """Interpret carriers through their profile and retain one immutable revision."""

    key = "process_evidence"
    label = "Retain processed evidence"
    outcomes = {"processed": "Processed", "source_hold": "Source hold"}

    def run(self, ctx: Any) -> Done:
        """Recheck authority, apply the profile and delegate retention atomically."""
        value = ctx.input
        model = apps.get_model("workflows_extraction.Extraction")
        target = ctx.load(apps.get_model(value.target_model), value.target_id)
        sources = [source.restore(ctx, i) for i, source in enumerate(value.prepared.sources)]
        parts, holds = value.prepared.collect(value.recognition)
        if holds != value.hold_reasons:
            raise ValidationError("The collected source hold changed.")
        profile = model.impl_field("profile").resolve_class(value.profile)()
        failure = None
        try:
            result = (
                Result({}, parts, {})
                if holds and not parts
                else profile.process_parts(
                    sources,
                    parts,
                    value.schema_,
                    config=value.profile_config,
                    recognition_used=any(part.kind == ExtractionPartKind.RECOGNIZED_TEXT for part in parts),
                )
            )
        except PipelineError as error:
            failure = error
            result = Result({}, tuple(error.parts) or parts, {}, provider_metadata={"unresolved_reasons": [error.code]})
        if holds:
            result = replace(
                result, provider_metadata={**(result.provider_metadata or {}), "unresolved_reasons": holds}
            )
        pages = [
            PageImage(page.source_position, page.page_position, "image/jpeg", b"", page.width, page.height, page.dpi)
            for page in value.prepared.pages
        ]
        evidence = model.objects.retain_result(
            sources=sources,
            pages=pages,
            page_results=[page.result(parts) for page in value.prepared.pages],
            result=result,
            target=target,
            actor=ctx.actor,
            profile=value.profile,
            profile_config=value.profile_config,
            schema=value.schema_,
            model=_model(ctx, value.model_id, ExtractionRole.MAPPING),
            recognition_model=_model(ctx, value.recognition_model_id, ExtractionRole.RECOGNITION),
            request_key=ctx.idempotency_key,
            failure=failure,
            error_code="source_hold" if holds else "",
            identity_mapping=value.identity_mapping,
            retired_identities=value.retired_identities,
        )
        # L2: ctx.artifact(evidence, "Document extraction evidence") when artifacts exist.
        return ctx.done(
            ProcessEvidenceOutput.from_extraction(evidence),
            outcome="processed" if evidence.status == "succeeded" else "source_hold",
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


class InferEvidenceStep(_ProviderStep, Step[InferEvidenceInput, InferEvidenceOutput, ProviderConfig]):
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
        """Authorize the base, call its provider and retain or report the current head."""
        value = ctx.input
        model = apps.get_model("workflows_extraction.Extraction")
        base = ctx.load(model, value.base_extraction_id)
        if base.revision != value.base_revision:
            raise ValidationError("The inference base revision changed.")
        target = ctx.load(apps.get_model(value.target_model), value.target_id)
        base.require_target(target)
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
        parts = base.document_parts()
        if base.error_code == "source_hold" or not parts:
            return ctx.done(
                InferEvidenceOutput(
                    **output, inference_failure={"stage": "acquisition", "code": RETAINED_CARRIER_UNAVAILABLE}
                ),
                outcome="source_unavailable",
            )
        sources = type(base).objects.authorized_document_sources(base, actor=ctx.actor)
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
                mapped = self.provider(ctx).infer(
                    parts, base.schema, inference_model, config=base.profile_config.get("mapping_config", {})
                )
                result = profile.normalize_inference_candidate(
                    sources,
                    parts,
                    base.schema,
                    value=mapped.value,
                    claims=mapped.claims,
                    metadata=mapped.provider_metadata,
                    config=base.profile_config,
                    recognition_used="recognition" in base.used_model_roles,
                )
            except PipelineError as error:
                failure = error
                result = Result(base.result, parts, dict(base.claims), provider_metadata=dict(base.stage_provenance))
            result = replace(
                result,
                used_model_roles=tuple(
                    sorted({*base.used_model_roles, *result.used_model_roles, ExtractionRole.MAPPING.value})
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
        # L2: ctx.artifact(evidence, "Inferred extraction evidence") when artifacts exist.
        outcome = "correspondence_required" if evidence.awaiting_correspondence else "inferred"
        if evidence.status != "succeeded" and not evidence.awaiting_correspondence:
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
