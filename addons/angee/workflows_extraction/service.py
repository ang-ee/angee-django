"""Transactional extraction service callable by workflows and domain operations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, prefetch_related_objects
from jsonschema import Draft202012Validator
from rebac import current_actor, system_context

from angee.base.actors import actor_user_id
from angee.base.refs import RecordRef, canonical_record_target, record_ref_for
from angee.base.scoping import read_scoped_queryset
from angee.base.serialization import canonical_json_sha256
from angee.workflows.attempts import json_values_equal
from angee.workflows.engine import external_operation_request
from angee.workflows_extraction.contracts import (
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    MappingResult,
    PageImage,
    PageResult,
)
from angee.workflows_extraction.enums import ExtractionErrorCode, ExtractionRole
from angee.workflows_extraction.inference import (
    RETAINED_AUTHORITY_COMPLETION_REVIEW,
    RETAINED_CARRIER_UNAVAILABLE,
    map_text_parts,
)
from angee.workflows_extraction.pointers import (
    JSON_POINTER_MISSING,
    implicit_identity_correspondence,
    json_pointer_value_or_missing,
    materialize_missing_json_pointer_path,
    set_json_pointer,
)
from angee.workflows_extraction.profiles import ExtractionProfile
from angee.workflows_extraction.routing import acquire_native_parts

if TYPE_CHECKING:
    from angee.storage.models import File


@dataclass(frozen=True, slots=True)
class PreparedPage:
    """One original page with READY carrier Files, including native pages."""

    source_position: int
    page_position: int
    native_parts: tuple[DocumentPart, ...]
    carrier_files: tuple[Any, ...]
    recognition_image: PageImage | None = None
    recognition_file: File | None = None

    def recognition_carrier(self) -> tuple[File, PageImage]:
        """Return the paired File and raster required by recognition consumers."""

        if self.recognition_file is None or self.recognition_image is None:
            raise ValidationError({"pages": "Recognition requires both its retained File and raster."})
        return self.recognition_file, self.recognition_image

    def recognition_input(self, *, model_id: str, config_digest: str) -> dict[str, Any]:
        """Project the retained carrier and frozen model policy into a page request."""

        image_file, image = self.recognition_carrier()
        return {
            "source_position": self.source_position,
            "page_position": self.page_position,
            "image_file_id": str(image_file.sqid),
            "image_digest": str(image_file.content_hash),
            "width": image.width,
            "height": image.height,
            "dpi": image.dpi,
            "model_id": model_id,
            "config_digest": config_digest,
        }


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    sources: tuple[DocumentSource, ...]
    pages: tuple[PreparedPage, ...]

    @property
    def recognition_pages(self) -> tuple[PreparedPage, ...]:
        return tuple(page for page in self.pages if page.recognition_file is not None)

    @property
    def manifest(self) -> dict[str, Any]:
        """Reference-only StepAttempt output; raw carriers remain in Storage."""

        return {
            "sources": [_source_fact(source) for source in self.sources],
            "pages": [
                {
                    "source_position": page.source_position,
                    "page_position": page.page_position,
                    "carrier_files": [
                        {"id": str(file.sqid), "digest": str(file.content_hash)} for file in page.carrier_files
                    ],
                    "recognition_file_id": (
                        str(page.recognition_file.sqid) if page.recognition_file is not None else None
                    ),
                    "width": page.recognition_image.width if page.recognition_image is not None else 0,
                    "height": page.recognition_image.height if page.recognition_image is not None else 0,
                    "dpi": page.recognition_image.dpi if page.recognition_image is not None else 0,
                }
                for page in self.pages
            ],
            "recognition_pages": [
                {
                    "source_position": page.source_position,
                    "page_position": page.page_position,
                    "image_file_id": str(page.recognition_carrier()[0].sqid),
                }
                for page in self.recognition_pages
            ],
        }


@dataclass(frozen=True, slots=True)
class CollectedDocument:
    parts: tuple[DocumentPart, ...]
    pages: tuple[PageImage, ...]
    page_results: tuple[PageResult, ...]
    recognition_used: bool
    hold_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SupersededInference:
    """A successor that won the retained revision race."""

    base_extraction_id: str
    current_extraction_id: str


@dataclass(frozen=True, slots=True)
class RetainedInference:
    """The extraction retained or reused by an inference operation."""

    extraction: Any


def prepare_pages(
    *,
    files: Sequence[Any],
    authorized_target: Any,
    profile: ExtractionProfile,
    message_parts: Sequence[Any] = (),
    config: Mapping[str, Any] | None = None,
) -> PreparedDocument:
    """Acquire all pages and retain native/raster carriers through Storage."""

    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    ordered_files, ordered_parts = tuple(files), tuple(message_parts)
    if not ordered_files and not ordered_parts:
        raise ValidationError({"files": "At least one extraction source is required."})
    if len({file.pk for file in ordered_files}) != len(ordered_files):
        raise ValidationError({"files": "Each source File may appear only once."})
    if len({part.pk for part in ordered_parts}) != len(ordered_parts):
        raise ValidationError({"message_parts": "Each Message Part may appear only once."})
    _authorize(ordered_files, ordered_parts, authorized_target, actor=actor)
    file_model = apps.get_model("storage", "File")
    if ordered_files:
        mime_model = file_model._meta.get_field("mime_type").remote_field.model
        prefetch_related_objects(
            ordered_files, Prefetch("mime_type", queryset=mime_model._base_manager.all()),
        )
    if ordered_parts:
        fragment_model = apps.get_model("messaging", "Part")._meta.get_field("fragment").remote_field.model
        prefetch_related_objects(
            ordered_parts, Prefetch("fragment", queryset=fragment_model._base_manager.all()),
        )
    sources = _document_sources(ordered_files, ordered_parts)
    options = _json_object(config or {}, field="config")
    acquired = acquire_native_parts(
        sources,
        profile=profile,
        **{
            key: int(options[key])
            for key in ("dpi", "max_edge", "max_pages", "max_text_bytes")
            if key in options
        },
    )
    owner_id = actor_user_id(actor)
    pages: list[PreparedPage] = []
    for page in acquired.pages:
        source = sources[page.source_position]
        drive_id = (
            str(cast(Any, source.file.drive).sqid)
            if source.file is not None
            else (
                str(cast(Any, authorized_target.drive).sqid)
                if authorized_target._meta.label_lower == "storage.file"
                else (str(authorized_target.sqid) if authorized_target._meta.label_lower == "storage.drive" else "")
            )
        )
        carriers: list[Any] = []
        for part in page.native_parts:
            if part.kind == ExtractionPartKind.STRUCTURED:
                if source.file is None:
                    raise ValidationError({"files": "Structured evidence requires its original File."})
                carriers.append(source.file)
            else:
                content = str(part.value).encode()
                carriers.append(
                    file_model.objects.ingest_bytes(
                        content,
                        filename=f"extraction-source-{page.source_position}-page-{page.page_position}.txt",
                        owner_id=owner_id,
                        drive_id=drive_id,
                    )
                )
        raster_file = None
        if page.recognition_image is not None:
            raster_file = file_model.objects.ingest_bytes(
                page.recognition_image.image_bytes,
                filename=f"extraction-source-{page.source_position}-page-{page.page_position}.jpg",
                owner_id=owner_id,
                drive_id=drive_id,
            )
            carriers.append(raster_file)
        if any(str(carrier.upload_state) != "ready" for carrier in carriers):
            raise ValidationError({"files": "Extraction carriers must be READY Files."})
        pages.append(
            PreparedPage(
                page.source_position,
                page.page_position,
                page.native_parts,
                tuple(carriers),
                page.recognition_image,
                raster_file,
            )
        )
    return PreparedDocument(sources, tuple(pages))


def restore_prepared_pages(
    manifest: Mapping[str, Any],
    *,
    files: Sequence[Any],
    authorized_target: Any,
    profile: ExtractionProfile,
    message_parts: Sequence[Any] = (),
    config: Mapping[str, Any] | None = None,
) -> PreparedDocument:
    """Recheck original bytes and READY carriers against the retained manifest."""

    prepared = prepare_pages(
        files=files,
        profile=profile,
        message_parts=message_parts,
        authorized_target=authorized_target,
        config=config,
    )
    if not json_values_equal(prepared.manifest, manifest):
        raise ValidationError({"pages": "The retained page manifest no longer matches its source carriers."})
    return prepared


def collect_carriers(
    prepared: PreparedDocument,
    map_results: Sequence[Mapping[str, Any]],
    *,
    recognition_model_id: str = "",
    recognition_config_digest: str = "",
) -> CollectedDocument:
    """Accept exactly one allowed recognition result per requested page."""

    requested = prepared.recognition_pages
    if len(map_results) > len(requested):
        raise ValidationError({"recognition": "Map added unrequested page results."})
    by_index: dict[int, Mapping[str, Any]] = {}
    for map_result in map_results:
        index = map_result.get("map_index")
        if type(index) is not int or index < 0 or index >= len(requested) or index in by_index:
            raise ValidationError({"recognition": "Map returned duplicate or invalid page positions."})
        by_index[index] = map_result
    if list(by_index) != sorted(by_index):
        raise ValidationError({"recognition": "Map page results are not in requested order."})
    recognized: dict[tuple[int, int], DocumentPart] = {}
    result_metadata: dict[tuple[int, int], dict[str, Any]] = {}
    present_sources = {page.source_position for page in prepared.pages}
    holds: list[str] = [
        f"source_{source.source_position}_has_no_pages"
        for source in prepared.sources
        if source.source_position not in present_sources
    ]
    for index, page in enumerate(requested):
        item = by_index.get(index)
        if item is None:
            holds.append(f"recognition_page_{page.source_position}_{page.page_position}_missing")
            continue
        if item.get("status") != "succeeded" or item.get("outcome") != "recognized":
            holds.append(f"recognition_page_{page.source_position}_{page.page_position}_failed")
            continue
        if item.get("output_present") is not True or not isinstance(item.get("output"), Mapping):
            holds.append(f"recognition_page_{page.source_position}_{page.page_position}_missing")
            continue
        image_file, image = page.recognition_carrier()
        output = item["output"]
        if (
            type(output.get("source_position")) is not int
            or type(output.get("page_position")) is not int
            or (output["source_position"], output["page_position"]) != (page.source_position, page.page_position)
            or str(output.get("image_file_id")) != str(image_file.sqid)
        ):
            raise ValidationError({"recognition": "A recognition result names a different page carrier."})
        text_file_id = output.get("text_file_id")
        request_key = output.get("request_key")
        if not isinstance(text_file_id, str) or not isinstance(request_key, str) or not request_key:
            holds.append(f"recognition_page_{page.source_position}_{page.page_position}_missing_text")
            continue
        if (
            str(output.get("model_id") or "") != recognition_model_id
            or str(output.get("config_digest") or "") != recognition_config_digest
        ):
            raise ValidationError({"recognition": "A recognition result used different model/configuration facts."})
        file_model = apps.get_model("storage", "File")
        actor = current_actor()
        readable = read_scoped_queryset(file_model, actor)
        if readable is None:
            raise PermissionDenied("Read access to the recognized carrier File is required.")
        text_file = readable.filter(sqid=text_file_id).first()
        if text_file is None or not text_file.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to the recognized carrier File is required.")
        if str(text_file.upload_state) != "ready":
            holds.append(f"recognition_page_{page.source_position}_{page.page_position}_unready")
            continue
        with text_file.open_stream() as stream:
            content = stream.read()
        if hashlib.sha256(content).hexdigest() != str(text_file.content_hash):
            raise ValidationError({"recognition": "A recognized carrier File digest changed."})
        recognition_facts = (
            (text_file.metadata or {}).get("workflows_extraction", {}).get("recognitions", {}).get(request_key)
        )
        expected_facts = {
            "image_file_id": str(image_file.sqid),
            "image_digest": str(image_file.content_hash),
            "text_digest": str(text_file.content_hash),
            "model_id": recognition_model_id,
            "config_digest": recognition_config_digest,
            "source_position": page.source_position,
            "page_position": page.page_position,
        }
        if not json_values_equal(recognition_facts, expected_facts):
            raise ValidationError({"recognition": "The retained recognized File lacks matching request provenance."})
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValidationError({"recognition": "A recognized carrier is not UTF-8 text."}) from error
        key = (page.source_position, page.page_position)
        recognized[key] = DocumentPart(
            *key,
            "text/plain",
            ExtractionPartKind.RECOGNIZED_TEXT,
            text,
            str(output.get("method") or "text_recognition"),
            str(text_file.content_hash),
            image.width,
            image.height,
            image.dpi,
            int(output.get("duration_ms") or 0),
            {
                "carrier_files": [str(text_file.sqid)],
                "image_file": str(image_file.sqid),
                "request_key": request_key,
            },
        )
        result_metadata[key] = {
            "carrier_files": [str(carrier.sqid) for carrier in (*page.carrier_files, text_file)],
            "recognition_model": str(output.get("model_id") or ""),
            "recognition_config_digest": str(output.get("config_digest") or ""),
        }
    parts: list[DocumentPart] = []
    retained_pages: list[PageImage] = []
    page_results: list[PageResult] = []
    for page in prepared.pages:
        key = (page.source_position, page.page_position)
        parts.extend(page.native_parts)
        if key in recognized:
            parts.append(recognized[key])
        raster = page.recognition_image
        retained_pages.append(raster or PageImage(*key, "text/plain", b"", 0, 0, 0))
        page_results.append(
            PageResult(
                {"status": "recognized" if key in recognized else "native" if raster is None else "held"},
                recognized[key].duration_ms if key in recognized else 0,
                result_metadata.get(key) or {"carrier_files": [str(carrier.sqid) for carrier in page.carrier_files]},
            )
        )
    return CollectedDocument(
        tuple(parts),
        tuple(retained_pages),
        tuple(page_results),
        bool(requested),
        tuple(holds),
    )


def process(
    prepared: PreparedDocument,
    map_results: Sequence[Mapping[str, Any]],
    *,
    schema: dict[str, Any],
    authorized_target: Any,
    profile: str,
    config: Mapping[str, Any] | None = None,
    model: Any | None = None,
    recognition_model: Any | None = None,
    identity_mapping: Mapping[str, str] | None = None,
    retired_identities: Mapping[str, str] | None = None,
) -> Any:
    """Retain one schema-valid deterministic profile result or explicit source hold."""

    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    for candidate, role in (
        (model, ExtractionRole.MAPPING),
        (recognition_model, ExtractionRole.RECOGNITION),
    ):
        if candidate is not None:
            candidate.require_usable(actor, role, uses=role.accepted_model_uses)
    requested_mapping = dict(identity_mapping or {})
    requested_retirement = dict(retired_identities or {})
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in requested_mapping.items()):
        raise ValidationError({"extraction": "Identity correspondence requires typed selectors and identities."})
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in requested_retirement.items()
    ):
        raise ValidationError({"extraction": "Retired identities require retained reasons."})
    files = tuple(source.file for source in prepared.sources if source.file is not None)
    message_parts = tuple(source.message_part for source in prepared.sources if source.message_part is not None)
    _authorize(files, message_parts, authorized_target, actor=actor)
    normalized_schema = _validated_schema(schema)
    normalized_config = _json_object(config or {}, field="config")
    profile_impl = extraction_model.impl_field("profile").resolve_class(profile)()
    profile_layout = _json_object(profile_impl.evidence_layout, field="evidence_layout")
    if "evidence_layout" in normalized_config and normalized_config["evidence_layout"] != profile_layout:
        raise ValidationError({"config": "The published profile owns its evidence layout."})
    normalized_config["evidence_layout"] = profile_layout
    prepared = restore_prepared_pages(
        prepared.manifest,
        files=files,
        profile=profile_impl,
        message_parts=message_parts,
        authorized_target=authorized_target,
        config=normalized_config,
    )
    collected = collect_carriers(
        prepared,
        map_results,
        recognition_model_id=str(recognition_model.sqid) if recognition_model is not None else "",
        recognition_config_digest=canonical_json_sha256(
            dict(normalized_config.get("recognition_config") or {})
        ),
    )
    schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
    if not schema_id:
        raise ValidationError({"schema": "Extraction schemas require a stable $id or x-version."})
    if len(collected.pages) != len(prepared.pages) or len(collected.page_results) != len(prepared.pages):
        raise ValidationError({"pages": "Every prepared page requires a retained carrier result."})
    source_facts = [_source_fact(source) for source in prepared.sources]
    target_ref = record_ref_for(authorized_target)
    lineage_key = _lineage_key(source_facts=source_facts, target_ref=target_ref)
    reuse_key = canonical_json_sha256(
        {
            "stage": "deterministic_process",
            "lineage": lineage_key,
            "source_facts": source_facts,
            "schema": normalized_schema,
            "profile": profile,
            "pipeline_version": str(profile_impl.pipeline_version),
            "model": _model_fingerprint(model),
            "recognition_model": _model_fingerprint(recognition_model),
            "config": normalized_config,
            "pages": [
                {
                    "source": page.source_position,
                    "page": page.page_position,
                    "carriers": [str(file.sqid) for file in page.carrier_files],
                }
                for page in prepared.pages
            ],
            "collected": [
                {
                    "source": page.source_position,
                    "page": page.page_position,
                    "result": page_result.value,
                    "metadata": page_result.provider_metadata,
                }
                for page, page_result in zip(collected.pages, collected.page_results)
            ],
            "parts": [
                {
                    "source": part.source_position,
                    "page": part.source_page,
                    "kind": part.kind,
                    "method": part.method,
                    "digest": part.content_hash,
                    "metadata": part.metadata,
                }
                for part in collected.parts
            ],
            "holds": list(collected.hold_reasons),
            "identity_mapping": requested_mapping,
            "retired_identities": requested_retirement,
        }
    )
    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    with system_context(reason="workflows_extraction.process.base"):
        existing = extraction_model._base_manager.filter(reuse_key=reuse_key).first()
        current_base = (
            extraction_model._base_manager.filter(lineage_key=lineage_key).order_by("-revision").first()
        )
        if existing is not None and existing.awaiting_correspondence:
            try:
                extraction_model.objects.inference_authority_base(existing, actor=actor)
            except ValidationError:
                authority = extraction_model.objects.latest_succeeded_identity_authority(
                    existing, actor=actor,
                )
                repair_reuse_key = canonical_json_sha256({
                    "stage": "deterministic_process_authority_repair",
                    "request_reuse_key": reuse_key,
                    "invalid_hold_id": str(existing.sqid),
                    "authority_id": str(authority.sqid),
                    "authority_revision": authority.revision,
                })
                repaired = extraction_model._base_manager.filter(
                    reuse_key=repair_reuse_key,
                ).first()
                if repaired is not None:
                    if (
                        extraction_model.objects.inference_authority_base(
                            repaired, actor=actor,
                        ).pk
                        != authority.pk
                    ):
                        raise ValidationError({
                            "extraction": "The retained authority repair owns different source facts."
                        })
                    reuse_key = repair_reuse_key
                    existing = repaired
                else:
                    if current_base is None or current_base.pk != existing.pk:
                        raise ValidationError({
                            "extraction": "The invalid retained hold is no longer the lineage head."
                        })
                    reuse_key = repair_reuse_key
                    existing = None
        if existing is None:
            expected_head_id = current_base.pk if current_base is not None else None
            correspondence_base = (
                extraction_model.objects.latest_succeeded_identity_authority(
                    current_base,
                    actor=actor,
                )
                if current_base is not None and current_base.awaiting_correspondence
                else current_base
            )
            expected_base_id = correspondence_base.pk if correspondence_base is not None else None
        else:
            expected_head_id = current_base.pk if current_base is not None else None
            expected_base_id = existing.provenance.get("identity_correspondence", {}).get("expected_base_id")
            correspondence_base = (
                extraction_model._base_manager.filter(pk=expected_base_id, lineage_key=lineage_key).first()
                if expected_base_id is not None
                else None
            )
    status, error_code = "succeeded", ""
    result: dict[str, Any] = {}
    claims: dict[str, list[dict[str, Any]]] = {}
    metadata: dict[str, Any] = {}
    roles: tuple[str, ...] = ()
    hold_reasons = [*collected.hold_reasons]
    if hold_reasons:
        status = "failed"
        error_code = (
            "source_hold:incomplete_recognition"
            if collected.hold_reasons
            else ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED
        )
        metadata = {"source_hold_reasons": hold_reasons}
    else:
        try:
            document_result = profile_impl.process_parts(
                prepared.sources,
                collected.parts,
                normalized_schema,
                config=normalized_config,
                recognition_used=collected.recognition_used,
            )
            _validate_document_result(
                document_result,
                source_count=len(prepared.sources),
                has_model=model is not None,
                has_recognition_model=recognition_model is not None,
            )
            result, claims = document_result.value, document_result.claims
            metadata, roles = dict(document_result.provider_metadata or {}), document_result.used_model_roles
            errors = sorted(
                Draft202012Validator(normalized_schema).iter_errors(result), key=lambda error: list(error.path)
            )
            if errors:
                raise ValidationError({"result": "Processing output does not match the declared schema."})
            # Recompute an exact retry against the base it originally named,
            # rather than a newer lineage head, before validating its retained
            # facts. New requests still correspond against the current head.
            if correspondence_base is not None and not requested_mapping and not requested_retirement:
                if (
                    implicit_identity_correspondence(
                        result,
                        layout=normalized_config.get("evidence_layout", {}),
                        original=correspondence_base,
                    )
                    is None
                ):
                    status = "failed"
                    error_code = ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED
                    hold_reasons.append("identity_correspondence_required")
                    metadata = {
                        **metadata,
                        "source_hold_reasons": list(hold_reasons),
                    }
        except DocumentPipelineError as error:
            status = "failed"
            error_code = ":".join(value for value in (error.stage, error.code) if value) or type(error).__name__
            metadata = {"failure": {"stage": error.stage, "code": error.code}, **error.metadata}
    target = canonical_record_target(authorized_target)
    return extraction_model.objects.create_revision(
        sources=prepared.sources,
        pages=collected.pages,
        page_results=collected.page_results,
        parts=collected.parts,
        lineage_key=lineage_key,
        reuse_key=reuse_key,
        expected_base_id=expected_base_id,
        expected_head_id=expected_head_id,
        identity_mapping=requested_mapping,
        retired_identities=requested_retirement,
        status=status,
        error_code=error_code,
        schema_id=schema_id,
        schema=normalized_schema,
        schema_digest=canonical_json_sha256(normalized_schema),
        profile=profile,
        model=model,
        recognition_model=recognition_model,
        profile_config=normalized_config,
        result=result,
        provenance={
            "source_count": len(source_facts),
            "page_count": len(prepared.pages),
            "completed_page_count": max(len(prepared.pages) - len(collected.hold_reasons), 0),
            "claims": claims,
            "document": {
                "stages": ["prepare_pages", "collect_carriers", "process_parts"],
                "config_digest": canonical_json_sha256(normalized_config),
                **metadata,
            },
            "unresolved_reasons": list(
                dict.fromkeys(
                    (*(result.get("routing_review_reasons") or ()), *hold_reasons)
                )
            ),
            "configured_model_roles": [
                role
                for role, candidate in (("mapping", model), ("recognition", recognition_model))
                if candidate is not None
            ],
            "used_model_roles": list(roles),
            "target": {"resource_type": target_ref.resource_type, "public_id": target_ref.public_id},
        },
        content_type=target.content_type,
        object_id=target.object_id,
        created_by_id=actor_user_id(actor),
    )


def infer(
    base: Any,
    *,
    model: Any,
    authorized_target: Any,
    operation_step_run: Any,
    identity_mapping: Mapping[str, str] | None = None,
    retired_identities: Mapping[str, str] | None = None,
) -> RetainedInference | SupersededInference:
    """Authorize the mapping model before retaining or reusing inferred facts."""

    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    correspondence_hold = isinstance(base, extraction_model) and base.awaiting_correspondence
    if (
        not isinstance(base, extraction_model)
        or base.pk is None
        or (base.status != "succeeded" and not correspondence_hold)
    ):
        raise ValidationError({"inference": "A successful retained base or exact correspondence hold is required."})
    operation_request = external_operation_request(operation_step_run)
    request_key = operation_request.request_key
    input_facts = operation_request.input
    requested_mapping = dict(identity_mapping or {})
    requested_retirement = dict(retired_identities or {})
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in requested_mapping.items()):
        raise ValidationError({"inference": "Document correspondence must contain typed selectors and identities."})
    if any(
        not isinstance(key, str) or not isinstance(value, str) or not value.strip()
        for key, value in requested_retirement.items()
    ):
        raise ValidationError({"inference": "Retired identities require typed retained reasons."})
    if not isinstance(input_facts, Mapping) or (
        input_facts.get("base_extraction_id") != str(base.sqid)
        or input_facts.get("base_revision") != base.revision
        or input_facts.get("model_id") != str(model.sqid)
        or input_facts.get("identity_mapping", {}) != requested_mapping
        or input_facts.get("retired_identities", {}) != requested_retirement
    ):
        raise ValidationError({"inference": "The admitted external request names different immutable facts."})
    if not base.with_actor(actor).has_access("read"):
        raise PermissionDenied("Read access to the base extraction is required.")
    if not authorized_target.with_actor(actor).has_access("read"):
        raise PermissionDenied("Read access to the extraction target is required.")
    model.require_usable(
        actor, ExtractionRole.MAPPING, uses=ExtractionRole.MAPPING.accepted_model_uses,
    )
    if base.model_id is not None and base.model_id != model.pk:
        raise ValidationError({"inference": "The inferred model differs from the frozen base policy."})
    config = _json_object(base.profile_config, field="config")
    if config.get("inference_mode") != "permitted":
        raise ValidationError({"inference": "This publication permits deterministic processing only."})
    profile = base.resolve_impl("profile")()
    inference_required = profile.inference_required(base.result, base.unresolved_reasons)
    if not correspondence_hold and not inference_required:
        raise ValidationError({"inference": "The retained base has no unresolved source facts."})
    automatic_correspondence = not requested_mapping and not requested_retirement
    preliminary_correspondence = (
        correspondence_hold
        and automatic_correspondence
        and "mapping" not in base.provenance.get("used_model_roles", ())
        and not base.stage_provenance.get("inference")
        and inference_required
    )
    if automatic_correspondence:
        effective_mapping = {}
    else:
        effective_mapping = requested_mapping
        prior_identities = {
            identity
            for document in base.document_refs
            for identity in (document.identity, *(line.identity for line in document.lines))
        }
        mapped_identities = [identity for identity in requested_mapping.values() if identity != "new"]
        if (
            len(mapped_identities) != len(set(mapped_identities))
            or set(mapped_identities) - prior_identities
            or set(requested_retirement) - prior_identities
            or set(mapped_identities).intersection(requested_retirement)
            or set(mapped_identities).union(requested_retirement) != prior_identities
        ):
            raise ValidationError(
                {"inference": "Reviewed correspondence must account for every prior document and line identity."}
            )
    if (
        correspondence_hold
        and not (requested_mapping or requested_retirement)
        and not preliminary_correspondence
    ):
        raise ValidationError({"inference": "The retained correspondence hold requires an explicit reviewed mapping."})
    target = canonical_record_target(authorized_target)
    if target.content_type.pk != base.content_type_id or str(target.object_id) != str(base.object_id):
        raise ValidationError({"inference": "The target differs from the retained base."})
    preliminary_authority = (
        extraction_model.objects.inference_authority_base(base, actor=actor)
        if preliminary_correspondence
        else None
    )
    reuse_key = canonical_json_sha256(
        {
            "stage": "bound_inference",
            "base_id": str(base.sqid),
            "base_revision": base.revision,
            "request_key": request_key,
            "model": _model_fingerprint(model),
            "mapping_config": config.get("mapping_config") or config,
        }
    )
    with system_context(reason="workflows_extraction.infer.current"):
        existing = extraction_model._base_manager.filter(reuse_key=reuse_key).first()
    if existing is not None:
        correspondence = existing.provenance.get("identity_correspondence", {})
        inference_facts = existing.stage_provenance.get("inference", {})
        expected_identity_base_id = (
            preliminary_authority.pk
            if preliminary_authority is not None and existing.awaiting_correspondence
            else base.pk
        )
        if (
            existing.lineage_key != base.lineage_key
            or existing.model_id != model.pk
            or existing.content_type_id != base.content_type_id
            or str(existing.object_id) != str(base.object_id)
            or correspondence.get("expected_base_id") != expected_identity_base_id
            or inference_facts.get("base_extraction_id") != str(base.sqid)
            or inference_facts.get("request_key") != request_key
            or inference_facts.get("mapping_config_digest")
            != canonical_json_sha256(config.get("mapping_config") or config)
            or inference_facts.get("requested_identity_mapping") != requested_mapping
            or inference_facts.get("requested_retirement") != requested_retirement
            or bool(inference_facts.get("automatic_correspondence")) != automatic_correspondence
        ):
            raise ValidationError({"inference": "The frozen request key owns different retained facts."})
        return RetainedInference(existing)
    current = extraction_model.objects.inference_current_head(base, actor=actor)
    if current.pk != base.pk:
        return SupersededInference(str(base.sqid), str(current.sqid))
    sources, parts = _retained_evidence(
        base,
        authorized_target=authorized_target,
        actor=actor,
    )
    if not parts:
        raise ValidationError({"inference": "The base has no complete retained carriers."})
    if any(row.result.get("status") == "held" for row in base.pages.order_by("position")):
        raise ValidationError({"inference": "Incomplete page carriers cannot be inferred."})
    authority_base = preliminary_authority or extraction_model.objects.inference_authority_base(
        base, actor=actor,
    )
    if authority_base.pk == base.pk:
        authority_sources, authority_parts = sources, parts
    else:
        authority_sources, authority_parts = _retained_evidence(
            authority_base,
            authorized_target=authorized_target,
            actor=actor,
        )
    claim_part_positions = _retained_claim_part_positions(
        authority_base,
        authority_sources=authority_sources,
        authority_parts=authority_parts,
        current_sources=sources,
        current_parts=parts,
        retired_identities=requested_retirement,
    )
    recognition_used = "recognition" in base.provenance.get("used_model_roles", ())
    usage_delta: dict[str, int] = {}
    mapping_result = None
    if correspondence_hold and not preliminary_correspondence:
        prior_inference = base.stage_provenance.get("inference", {})
        request_metadata = dict(prior_inference.get("provider") or {})
        document_result = DocumentResult(
            value=deepcopy(base.result),
            parts=parts,
            claims=deepcopy(base.claims),
            used_model_roles=tuple(base.provenance.get("used_model_roles", ())),
            provider_metadata=request_metadata,
        )
    else:
        timeout = config.get("timeout", settings.ANGEE_EXTRACTION_TIMEOUT_SECONDS)
        mapping_result = map_text_parts(
            parts,
            _validated_schema(base.schema),
            step_run=operation_step_run,
            model=model,
            config=dict(config.get("mapping_config") or config),
            timeout=timeout,
        )
        usage_delta = dict(mapping_result.usage_delta)
        try:
            document_result = profile.normalize_inference_candidate(
                sources,
                parts,
                base.schema,
                value=mapping_result.value,
                claims=mapping_result.claims,
                metadata=mapping_result.provider_metadata,
                config=config,
                recognition_used=recognition_used,
            )
        except DocumentPipelineError as error:
            failure = DocumentPipelineError(
                str(error),
                parts=error.parts or parts,
                stage=error.stage,
                code=error.code,
                metadata=error.metadata,
                usage_delta=usage_delta,
            )
            return _retain_failed_inference(
                base,
                authority_base=authority_base,
                model=model,
                actor=actor,
                config=config,
                reuse_key=reuse_key,
                request_key=request_key,
                requested_mapping=requested_mapping,
                requested_retirement=requested_retirement,
                automatic_correspondence=automatic_correspondence,
                mapping_result=mapping_result,
                error=failure,
            )
        except ValidationError:
            failure = DocumentPipelineError(
                "The inferred candidate failed profile normalization.",
                parts=parts,
                stage="inference",
                code="candidate_normalization_failed",
                usage_delta=usage_delta,
            )
            return _retain_failed_inference(
                base,
                authority_base=authority_base,
                model=model,
                actor=actor,
                config=config,
                reuse_key=reuse_key,
                request_key=request_key,
                requested_mapping=requested_mapping,
                requested_retirement=requested_retirement,
                automatic_correspondence=automatic_correspondence,
                mapping_result=mapping_result,
                error=failure,
            )
    if document_result.parts != parts:
        return _retain_failed_inference(
            base,
            authority_base=authority_base,
            model=model,
            actor=actor,
            config=config,
            reuse_key=reuse_key,
            request_key=request_key,
            requested_mapping=requested_mapping,
            requested_retirement=requested_retirement,
            automatic_correspondence=automatic_correspondence,
            mapping_result=mapping_result,
            error=DocumentPipelineError(
                "The inferred candidate changed the retained carrier ordering.",
                parts=parts,
                stage="inference",
                code="carrier_order_changed",
                usage_delta=usage_delta,
            ),
        )
    correspondence_required = False
    try:
        if automatic_correspondence:
            automatic_mapping = implicit_identity_correspondence(
                document_result.value,
                layout=config.get("evidence_layout", {}),
                original=(authority_base if preliminary_correspondence else base),
            )
            correspondence_required = automatic_mapping is None
            effective_mapping = automatic_mapping or {}
        if not correspondence_required:
            final_value, final_claims, completed_missing_authority = _preserve_retained_authority(
                authority_base,
                document_result.value,
                document_result.claims,
                identity_mapping=effective_mapping,
                retired_identities=requested_retirement,
                claim_part_positions=claim_part_positions,
            )
            document_result = DocumentResult(
                final_value,
                document_result.parts,
                final_claims,
                document_result.used_model_roles,
                document_result.duration_ms,
                document_result.provider_metadata,
            )
        else:
            completed_missing_authority = False
        _validate_document_result(
            document_result,
            source_count=len(sources),
            has_model=True,
            has_recognition_model=base.recognition_model_id is not None,
        )
    except ValidationError:
        failure = DocumentPipelineError(
            "The inferred candidate failed retained-evidence validation.",
            parts=document_result.parts,
            stage="inference",
            code="candidate_validation_failed",
            usage_delta=usage_delta,
        )
        return _retain_failed_inference(
            base,
            authority_base=authority_base,
            model=model,
            actor=actor,
            config=config,
            reuse_key=reuse_key,
            request_key=request_key,
            requested_mapping=requested_mapping,
            requested_retirement=requested_retirement,
            automatic_correspondence=automatic_correspondence,
            mapping_result=mapping_result,
            error=failure,
        )
    errors = sorted(
        Draft202012Validator(base.schema).iter_errors(document_result.value), key=lambda error: list(error.path)
    )
    if errors:
        return _retain_failed_inference(
            base,
            authority_base=authority_base,
            model=model,
            actor=actor,
            config=config,
            reuse_key=reuse_key,
            request_key=request_key,
            requested_mapping=requested_mapping,
            requested_retirement=requested_retirement,
            automatic_correspondence=automatic_correspondence,
            mapping_result=mapping_result,
            error=DocumentPipelineError(
                "The inferred candidate does not match the frozen schema.",
                parts=document_result.parts,
                stage="inference",
                code="candidate_schema_mismatch",
                usage_delta=usage_delta,
            ),
        )
    unresolved_reasons = list(
        document_result.value.get("routing_review_reasons") or ()
    )
    if completed_missing_authority:
        unresolved_reasons = list(
            dict.fromkeys(
                (*unresolved_reasons, RETAINED_AUTHORITY_COMPLETION_REVIEW)
            )
        )
    if correspondence_required:
        unresolved_reasons = list(
            dict.fromkeys((*unresolved_reasons, "identity_correspondence_required"))
        )
    provenance = {
        **base.provenance,
        "claims": document_result.claims,
        "used_model_roles": list(document_result.used_model_roles),
        "unresolved_reasons": unresolved_reasons,
        "document": {
            **dict(base.stage_provenance),
            "stages": ["prepare_pages", "collect_carriers", "process_parts", "infer"],
            "inference": {
                "base_extraction_id": str(base.sqid),
                "base_revision": base.revision,
                "authority_extraction_id": str(authority_base.sqid),
                "authority_revision": authority_base.revision,
                "request_key": request_key,
                "mapping_config_digest": canonical_json_sha256(
                    config.get("mapping_config") or config
                ),
                "requested_identity_mapping": requested_mapping,
                "requested_retirement": requested_retirement,
                "automatic_correspondence": automatic_correspondence,
                "effective_identity_mapping": effective_mapping,
                "provider": dict(document_result.provider_metadata or {}),
            },
        },
    }
    try:
        extraction = extraction_model.objects.create_revision_from_evidence(
            base,
            lineage_key=base.lineage_key,
            reuse_key=reuse_key,
            expected_base_id=(authority_base.pk if preliminary_correspondence and correspondence_required else base.pk),
            expected_head_id=base.pk,
            identity_mapping=effective_mapping,
            retired_identities=requested_retirement,
            status="failed" if correspondence_required else "succeeded",
            error_code=(ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED if correspondence_required else ""),
            schema_id=base.schema_id,
            schema=base.schema,
            schema_digest=base.schema_digest,
            profile=str(base.profile),
            model=model,
            recognition_model_id=base.recognition_model_id,
            profile_config=config,
            result=document_result.value,
            provenance=provenance,
            content_type_id=base.content_type_id,
            object_id=base.object_id,
            created_by_id=actor_user_id(actor),
        )
        return RetainedInference(extraction)
    except ValidationError as error:
        current = extraction_model.objects.inference_current_head(base, actor=actor)
        if current.pk != base.pk:
            return SupersededInference(
                str(base.sqid),
                str(current.sqid),
            )
        if usage_delta:
            raise DocumentPipelineError(
                "The inferred candidate could not be retained.",
                parts=document_result.parts,
                stage="inference",
                code="candidate_retention_failed",
                usage_delta=usage_delta,
            ) from error
        raise


def _retain_failed_inference(
    base: Any,
    *,
    authority_base: Any,
    model: Any,
    actor: Any,
    config: Mapping[str, Any],
    reuse_key: str,
    request_key: str,
    requested_mapping: Mapping[str, str],
    requested_retirement: Mapping[str, str],
    automatic_correspondence: bool,
    mapping_result: MappingResult | None,
    error: DocumentPipelineError,
) -> RetainedInference | SupersededInference:
    """Retain a post-provider failure as the idempotent inference successor."""

    extraction_model = type(base)
    usage_delta = dict(error.usage_delta)
    error_code = ":".join(
        value for value in (error.stage, error.code) if value
    ) or type(error).__name__
    provider = {
        **(mapping_result.provider_metadata if mapping_result is not None else {}),
        "usage": usage_delta,
    }
    inference = {
        "base_extraction_id": str(base.sqid),
        "base_revision": base.revision,
        "authority_extraction_id": str(authority_base.sqid),
        "authority_revision": authority_base.revision,
        "request_key": request_key,
        "mapping_config_digest": canonical_json_sha256(
            config.get("mapping_config") or config
        ),
        "requested_identity_mapping": dict(requested_mapping),
        "requested_retirement": dict(requested_retirement),
        "automatic_correspondence": automatic_correspondence,
        "effective_identity_mapping": {},
        "provider": provider,
    }
    provenance = {
        **base.provenance,
        "claims": {},
        "used_model_roles": list(
            dict.fromkeys((*base.provenance.get("used_model_roles", ()), "mapping"))
        ),
        "unresolved_reasons": [error_code],
        "document": {
            **dict(base.stage_provenance),
            "stages": ["prepare_pages", "collect_carriers", "process_parts", "infer"],
            "failure": {
                "type": type(error).__name__,
                "message": str(error),
                "stage": error.stage,
                "code": error.code,
                "metadata": dict(error.metadata),
            },
            "inference": inference,
        },
    }
    try:
        extraction = extraction_model.objects.create_revision_from_evidence(
            base,
            lineage_key=base.lineage_key,
            reuse_key=reuse_key,
            expected_base_id=base.pk,
            expected_head_id=base.pk,
            identity_mapping={},
            retired_identities={},
            status="failed",
            error_code=error_code,
            schema_id=base.schema_id,
            schema=base.schema,
            schema_digest=base.schema_digest,
            profile=str(base.profile),
            model=model,
            recognition_model_id=base.recognition_model_id,
            profile_config=dict(config),
            result={},
            provenance=provenance,
            content_type_id=base.content_type_id,
            object_id=base.object_id,
            created_by_id=actor_user_id(actor),
        )
        return RetainedInference(extraction)
    except ValidationError as retention_error:
        current = extraction_model.objects.inference_current_head(base, actor=actor)
        if current.pk != base.pk:
            return SupersededInference(
                str(base.sqid), str(current.sqid),
            )
        if usage_delta:
            raise DocumentPipelineError(
                "The failed inferred candidate could not be retained.",
                parts=error.parts,
                stage="inference",
                code="candidate_retention_failed",
                metadata=error.metadata,
                usage_delta=usage_delta,
            ) from retention_error
        raise


def _retained_evidence(
    base: Any,
    *,
    authorized_target: Any,
    actor: Any,
) -> tuple[tuple[DocumentSource, ...], tuple[DocumentPart, ...]]:
    """Reconstruct profile inputs from immutable retained rows under actor reads."""

    with system_context(reason="workflows_extraction.retained_evidence"):
        source_rows = list(
            base.sources
            .select_related("file__mime_type", "message_part__fragment", "message_part__message")
            .order_by("position")
        )
        part_rows = list(base.parts.select_related("source").order_by("position"))
    files = tuple(row.file for row in source_rows if row.file_id is not None)
    message_parts = tuple(row.message_part for row in source_rows if row.message_part_id is not None)
    _authorize(files, message_parts, authorized_target, actor=actor)
    sources = tuple(
        DocumentSource(
            row.position,
            str(row.content_hash),
            str(row.file.mime_type.mime_type) if row.file_id is not None else str(row.message_part.type),
            b"" if row.file_id is not None else "",
            file=row.file,
            message_part=row.message_part,
        )
        for row in source_rows
    )
    sources_by_id = {row.pk: source for row, source in zip(source_rows, sources, strict=True)}
    parts = tuple(
        DocumentPart(
            sources_by_id[row.source_id].source_position,
            row.source_page,
            row.mime_type,
            row.kind,
            row.value,
            row.method,
            row.content_hash,
            row.width,
            row.height,
            row.dpi,
            row.duration_ms,
            row.metadata,
        )
        for row in part_rows
    )
    return sources, parts


def _preserve_retained_authority(
    base: Any,
    candidate: dict[str, Any],
    claims: dict[str, list[dict[str, Any]]],
    *,
    identity_mapping: Mapping[str, str],
    retired_identities: Mapping[str, str],
    claim_part_positions: Mapping[int, int],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], bool]:
    """Provider output can only fill paths lacking source or Decision authority."""

    result, retained_claims = deepcopy(candidate), deepcopy(claims)
    completed_missing_authority = False
    protected = set(pointer for pointer, entries in base.claims.items() if entries)
    for correction in base.corrections:
        protected.update(correction.corrected_paths)
    old_selectors = [
        (item.selector, item.identity) for document in base.document_refs for item in (document, *document.lines)
    ]
    mapped = {identity: selector for selector, identity in identity_mapping.items() if identity != "new"}
    for pointer in sorted(protected, key=lambda item: (len(item), item)):
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValidationError({"inference": "The retained authority pointers are invalid."})
        matched = _authority_identity(pointer, old_selectors)
        if matched is not None and matched[1] in retired_identities:
            continue
        if any(selector and selector.startswith(f"{pointer}/") for selector, _identity in old_selectors):
            raise ValidationError(
                {"inference": "Authoritative facts cannot cover a document or line identity container."}
            )
        old_value = json_pointer_value_or_missing(base.result, pointer)
        if old_value is JSON_POINTER_MISSING:
            raise ValidationError({"inference": "A retained authoritative fact is absent."})
        destination = pointer
        if matched is not None:
            selector, identity = matched
            if identity not in mapped:
                raise ValidationError({"inference": "Authoritative facts need explicit document correspondence."})
            destination = mapped[identity] + pointer[len(selector) :]
        if json_pointer_value_or_missing(result, destination) is JSON_POINTER_MISSING:
            try:
                materialize_missing_json_pointer_path(
                    result,
                    destination,
                    source=base.result,
                    source_pointer=pointer,
                )
            except KeyError as error:
                raise ValidationError(
                    {"inference": "The candidate omitted an authoritative fact path."}
                ) from error
            completed_missing_authority = True
        set_json_pointer(result, destination, deepcopy(old_value))
        retained_claims = {
            path: entries
            for path, entries in retained_claims.items()
            if path != destination and not path.startswith(f"{destination}/")
        }
        if pointer in base.claims:
            retained_claims[destination] = [
                {
                    **deepcopy(claim),
                    "part_position": claim_part_positions[claim["part_position"]],
                }
                for claim in base.claims[pointer]
            ]
    return result, retained_claims, completed_missing_authority


def _retained_claim_part_positions(
    authority_base: Any,
    *,
    authority_sources: Sequence[DocumentSource],
    authority_parts: Sequence[DocumentPart],
    current_sources: Sequence[DocumentSource],
    current_parts: Sequence[DocumentPart],
    retired_identities: Mapping[str, str],
) -> dict[int, int]:
    """Map retained source claims through immutable carrier facts, never positions."""

    selectors = [
        (item.selector, item.identity)
        for document in authority_base.document_refs
        for item in (document, *document.lines)
    ]
    required_positions: set[int] = set()
    for pointer, entries in authority_base.claims.items():
        matched = _authority_identity(pointer, selectors)
        if matched is not None and matched[1] in retired_identities:
            continue
        for claim in entries:
            position = claim.get("part_position")
            if type(position) is not int or position < 0 or position >= len(authority_parts):
                raise ValidationError({"inference": "A retained source claim references unavailable evidence."})
            required_positions.add(position)
    if authority_parts is current_parts:
        return {position: position for position in required_positions}

    current_positions: dict[tuple[Any, ...], list[int]] = {}
    for position, part in enumerate(current_parts):
        current_positions.setdefault(
            _carrier_identity(current_sources, part),
            [],
        ).append(position)
    mapped: dict[int, int] = {}
    for position in required_positions:
        matches = current_positions.get(
            _carrier_identity(authority_sources, authority_parts[position]),
            (),
        )
        if len(matches) != 1:
            raise DocumentPipelineError(
                "Previously retained facts cannot be matched to this source set. "
                "Review the original and current evidence before continuing.",
                parts=current_parts,
                stage="correspondence",
                code=RETAINED_CARRIER_UNAVAILABLE,
            )
        mapped[position] = matches[0]
    return mapped


def _carrier_identity(
    sources: Sequence[DocumentSource],
    part: DocumentPart,
) -> tuple[Any, ...]:
    """Return physical source/part facts that survive source ordering changes."""

    by_position = {source.source_position: source for source in sources}
    source = by_position.get(part.source_position)
    if source is None:
        raise ValidationError({"inference": "Retained evidence references an unavailable source."})
    if source.file is not None:
        source_identity = ("file", str(source.file.sqid))
    elif source.message_part is not None:
        source_identity = ("message_part", str(source.message_part.sqid))
    else:
        raise ValidationError({"inference": "Retained evidence lacks an immutable source identity."})
    return (
        *source_identity,
        source.content_hash,
        part.source_page,
        part.mime_type,
        part.kind,
        part.method,
        part.content_hash,
        part.width,
        part.height,
        part.dpi,
        canonical_json_sha256({"value": part.value}),
    )


def _authority_identity(
    pointer: str,
    selectors: Sequence[tuple[str, str]],
) -> tuple[str, str] | None:
    matched = sorted(
        (
            (selector, identity)
            for selector, identity in selectors
            if selector == "" or pointer == selector or pointer.startswith(f"{selector}/")
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    return matched[0] if matched else None


def _reviewed_correction_unresolved_reasons(
    provenance: Mapping[str, Any],
) -> list[str]:
    """Clear only the generic hold discharged by an authorized human correction."""

    unresolved = provenance.get("unresolved_reasons", [])
    if not isinstance(unresolved, list):
        raise ValidationError(
            {"extraction": "The retained unresolved-reason provenance is invalid."}
        )
    return [
        str(reason)
        for reason in unresolved
        if str(reason) != RETAINED_AUTHORITY_COMPLETION_REVIEW
    ]


def _retained_source_facts(sources: Sequence[Any]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    seen_files: set[Any] = set()
    seen_parts: set[Any] = set()
    for position, source in enumerate(sources):
        if source.position != position:
            raise ValidationError({"extraction": "The retained source ordering is invalid."})
        content_hash = str(source.content_hash).lower()
        if source.file_id is not None and source.message_part_id is None:
            if source.file_id in seen_files or content_hash != str(source.file.content_hash).lower():
                raise ValidationError({"extraction": "A retained file source identity is invalid."})
            seen_files.add(source.file_id)
            identity = {"file": str(source.file.sqid)}
        elif source.message_part_id is not None and source.file_id is None:
            if source.message_part_id in seen_parts:
                raise ValidationError({"extraction": "A retained message source identity is invalid."})
            part = source.message_part
            fragment_hash = _message_part_hash(part)
            if (
                content_hash != fragment_hash
                or hashlib.sha256(str(part.fragment.text).encode()).hexdigest() != fragment_hash
            ):
                raise ValidationError({"extraction": "A retained message source identity is invalid."})
            seen_parts.add(source.message_part_id)
            identity = {"message_part": str(part.sqid)}
        else:
            raise ValidationError({"extraction": "A retained source must name exactly one input."})
        facts.append({"position": position, **identity, "content_hash": content_hash})
    if not facts:
        raise ValidationError({"extraction": "The retained extraction has no source evidence."})
    return facts


def _unchanged_claims(
    claims: Any,
    *,
    before: Any,
    after: Any,
    original_refs: Any = (),
    identity_mapping: Mapping[str, str] | None = None,
    retired_identities: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Move equal claims only where reviewed logical correspondence proves identity."""

    if not isinstance(claims, Mapping):
        raise ValidationError({"extraction": "The retained extraction claims are invalid."})
    retained: dict[str, Any] = {}
    old_selectors = [
        (selector, identity)
        for ref in original_refs
        for selector, identity in (
            (ref.selector, ref.identity),
            *((line.selector, line.identity) for line in ref.lines),
        )
    ]
    new_selectors = {identity: selector for selector, identity in (identity_mapping or {}).items() if identity != "new"}
    retired = set(retired_identities or {})
    for pointer, entries in claims.items():
        if not isinstance(pointer, str) or not pointer.startswith("/") or not isinstance(entries, list):
            raise ValidationError({"extraction": "The retained extraction claims are invalid."})
        if any(
            not isinstance(entry, Mapping) or type(entry.get("part_position")) is not int or entry["part_position"] < 0
            for entry in entries
        ):
            raise ValidationError({"extraction": "The retained extraction claims are invalid."})
        old_value = json_pointer_value_or_missing(before, pointer)
        mapped_pointer = pointer
        matched = _authority_identity(pointer, old_selectors)
        if matched is not None and matched[1] in retired:
            continue
        if matched is not None and matched[1] in new_selectors:
            selector, identity = matched
            mapped_pointer = new_selectors[identity] + pointer[len(selector) :]
        elif matched is not None and matched[1] not in new_selectors:
            continue
        new_value = json_pointer_value_or_missing(
            after,
            mapped_pointer,
            array_element_baseline=(
                before if mapped_pointer == pointer and (matched is None or matched[0] == "") else JSON_POINTER_MISSING
            ),
        )
        if (
            old_value is not JSON_POINTER_MISSING
            and new_value is not JSON_POINTER_MISSING
            and json_values_equal(old_value, new_value)
        ):
            retained[mapped_pointer] = entries
    return _json_object(retained, field="extraction")


def _unchanged_corrections(
    corrections: Sequence[Mapping[str, Any]],
    *,
    before: Any,
    after: Any,
    original_refs: Any,
    identity_mapping: Mapping[str, str],
    retired_identities: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Carry prior human authority only through exact logical identity correspondence."""

    selectors = [
        (selector, identity)
        for ref in original_refs
        for selector, identity in (
            (ref.selector, ref.identity),
            *((line.selector, line.identity) for line in ref.lines),
        )
    ]
    mapped = {identity: selector for selector, identity in identity_mapping.items() if identity != "new"}
    retired = set(retired_identities)
    carried: list[dict[str, Any]] = []
    for correction in corrections:
        paths = correction.get("corrected_paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValidationError({"extraction": "The retained correction provenance is invalid."})
        retained_paths: list[str] = []
        for pointer in paths:
            matched = _authority_identity(pointer, selectors)
            if matched is not None and matched[1] in retired:
                continue
            destination = pointer
            if matched is not None:
                selector, identity = matched
                if identity not in mapped:
                    raise ValidationError(
                        {"extraction": "Prior correction authority needs explicit identity correspondence."}
                    )
                destination = mapped[identity] + pointer[len(selector) :]
            old_value = json_pointer_value_or_missing(before, pointer)
            new_value = json_pointer_value_or_missing(after, destination)
            if (
                old_value is not JSON_POINTER_MISSING
                and new_value is not JSON_POINTER_MISSING
                and json_values_equal(old_value, new_value)
            ):
                retained_paths.append(destination)
        carried.append({**deepcopy(correction), "corrected_paths": retained_paths})
    return carried


def _leaf_json_pointers(value: Any, *, pointer: str) -> tuple[str, ...]:
    """Return stable leaf pointers so newly reviewed identities gain Decision authority."""

    if isinstance(value, Mapping):
        return tuple(
            child
            for key in sorted(value)
            for child in _leaf_json_pointers(
                value[key],
                pointer=f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}",
            )
        )
    if isinstance(value, list):
        return tuple(
            child
            for index, item in enumerate(value)
            for child in _leaf_json_pointers(item, pointer=f"{pointer}/{index}")
        )
    return (pointer,)


def _changed_fact_pointers(
    before: Any,
    after: Any,
    *,
    original_refs: Any,
    identity_mapping: Mapping[str, str],
) -> set[str]:
    """Compare retained scalar facts through logical identities, never array positions."""

    original_selectors = {
        identity: selector
        for ref in original_refs
        for selector, identity in (
            (ref.selector, ref.identity),
            *((line.selector, line.identity) for line in ref.lines),
        )
    }
    current_selectors = list(identity_mapping.items())
    changed: set[str] = set()
    for pointer in _leaf_json_pointers(after, pointer=""):
        matched = _authority_identity(pointer, current_selectors)
        before_pointer = pointer
        if matched is not None:
            selector, identity = matched
            if identity == "new":
                changed.add(pointer)
                continue
            original_selector = original_selectors.get(identity)
            if original_selector is None:
                raise ValidationError({"extraction": "Correction authority needs exact identity correspondence."})
            before_pointer = original_selector + pointer[len(selector) :]
        before_value = json_pointer_value_or_missing(before, before_pointer)
        after_value = json_pointer_value_or_missing(after, pointer)
        if before_value is JSON_POINTER_MISSING or not json_values_equal(before_value, after_value):
            changed.add(pointer)
    return changed


def _confirmed_fact_pointers(
    requested: Sequence[str],
    *,
    before: Any,
    after: Any,
    original_refs: Any,
    identity_mapping: Mapping[str, str],
    retired_identities: Mapping[str, str],
) -> set[str]:
    """Validate unchanged scalar facts explicitly authorized by the Decision."""

    if isinstance(requested, (str, bytes)) or not isinstance(requested, Sequence):
        raise ValidationError({"confirmed_paths": "Confirmed fact paths must be a list."})
    paths = list(requested)
    if not all(isinstance(pointer, str) and pointer.startswith("/") for pointer in paths):
        raise ValidationError({"confirmed_paths": "Confirmed fact paths must be RFC 6901 pointers."})
    if len(paths) != len(set(paths)):
        raise ValidationError({"confirmed_paths": "Confirmed fact paths must be unique."})

    original_selector_pairs = [
        (identity, selector)
        for ref in original_refs
        for selector, identity in (
            (ref.selector, ref.identity),
            *((line.selector, line.identity) for line in ref.lines),
        )
    ]
    original_selectors = dict(original_selector_pairs)
    original_identity_counts: dict[str, int] = {}
    for identity, _selector in original_selector_pairs:
        original_identity_counts[identity] = original_identity_counts.get(identity, 0) + 1
    current_selectors = list(identity_mapping.items())
    current_identity_counts: dict[str, int] = {}
    for identity in identity_mapping.values():
        current_identity_counts[identity] = current_identity_counts.get(identity, 0) + 1
    retired = set(retired_identities)
    confirmed: set[str] = set()
    for pointer in paths:
        after_value = json_pointer_value_or_missing(after, pointer)
        if after_value is JSON_POINTER_MISSING:
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} is absent."})
        if isinstance(after_value, (Mapping, list)):
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} is not scalar."})
        matched = _authority_identity(pointer, current_selectors)
        if matched is None:
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} lacks a logical identity."})
        selector, identity = matched
        if identity == "new":
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} belongs to a new identity."})
        if identity in retired:
            raise ValidationError(
                {"confirmed_paths": f"Confirmed fact path {pointer!r} belongs to a retired identity."}
            )
        if (
            current_identity_counts.get(identity) != 1
            or original_identity_counts.get(identity) != 1
            or identity not in original_selectors
        ):
            raise ValidationError(
                {"confirmed_paths": f"Confirmed fact path {pointer!r} has ambiguous identity correspondence."}
            )
        before_pointer = original_selectors[identity] + pointer[len(selector) :]
        before_value = json_pointer_value_or_missing(before, before_pointer)
        if before_value is JSON_POINTER_MISSING:
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} is absent."})
        if isinstance(before_value, (Mapping, list)):
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} is not scalar."})
        if not json_values_equal(before_value, after_value):
            raise ValidationError({"confirmed_paths": f"Confirmed fact path {pointer!r} changed value."})
        confirmed.add(pointer)
    return confirmed


def authored_profile_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return authored profile policy without native retry lineage metadata."""

    return {key: value for key, value in dict(config).items() if key != "retry_of_revision"}


def _authorize(files: Sequence[Any], message_parts: Sequence[Any], target: Any, *, actor: Any) -> None:
    if not target.with_actor(actor).has_access("read"):
        raise PermissionDenied("Read access to the extraction target is required.")
    if any(not file.with_actor(actor).has_access("read") for file in files):
        raise PermissionDenied("Read access to every extraction source is required.")
    if any(not part.with_actor(actor).has_access("read") for part in message_parts):
        raise PermissionDenied("Read access to every extraction source is required.")


def _message_part_hash(part: Any) -> str:
    if part.fragment_id is None:
        raise ValidationError({"message_parts": "Text message parts require a retained fragment."})
    if not str(part.type).startswith("text/"):
        raise ValidationError({"message_parts": "Only textual message parts can be extraction sources."})
    if str(part.role) not in {"body", "title", "quoted", "signature", "header"}:
        raise ValidationError({"message_parts": "The message part has an unsupported textual role."})
    if len(part.fragment.text.encode()) > int(settings.ANGEE_EXTRACTION_MAX_BYTES):
        raise ValidationError({"message_parts": "An extraction source exceeds the configured byte limit."})
    return str(part.fragment.hash)


def _document_sources(files: Sequence[Any], message_parts: Sequence[Any]) -> tuple[DocumentSource, ...]:
    values = []
    for i, file in enumerate(files):
        with file.open_stream() as stream:
            content = stream.read(int(settings.ANGEE_EXTRACTION_MAX_BYTES) + 1)
        if len(content) > int(settings.ANGEE_EXTRACTION_MAX_BYTES):
            raise ValidationError({"files": "An extraction source exceeds the configured byte limit."})
        content_hash = hashlib.sha256(content).hexdigest()
        if content_hash != str(file.content_hash).lower() or len(content) != int(file.size_bytes):
            raise ValidationError({"files": "An extraction source no longer matches its retained identity."})
        mime_type = str(getattr(getattr(file, "mime_type", None), "mime_type", "") or "")
        values.append(DocumentSource(i, content_hash, mime_type, content, file=file))
    for i, part in enumerate(message_parts):
        text = str(part.fragment.text)
        content_hash = _message_part_hash(part)
        if hashlib.sha256(text.encode()).hexdigest() != content_hash:
            raise ValidationError({"message_parts": "An extraction source no longer matches its retained identity."})
        values.append(DocumentSource(len(files) + i, content_hash, str(part.type), text, message_part=part))
    return tuple(values)


def _source_fact(source: DocumentSource) -> dict[str, Any]:
    if source.file is not None:
        identity = {"file": str(source.file.sqid)}
    elif source.message_part is not None:
        identity = {"message_part": str(source.message_part.sqid)}
    else:
        raise ValueError("Document source identity unavailable.")
    return {"position": source.source_position, **identity, "content_hash": source.content_hash}


def _lineage_key(*, source_facts: Sequence[Mapping[str, Any]], target_ref: RecordRef) -> str:
    """Anchor logical lineage to the canonical authorized target.

    A File target also names its original source when it occurs in the set.
    Schema, source membership/order and bytes belong to exact revision facts.
    """

    if not source_facts:
        raise ValueError("An original extraction source is required.")
    identities = sorted(str(fact.get("file") or fact.get("message_part") or "") for fact in source_facts)
    if not identities or not all(identities):
        raise ValueError("The original extraction source identity is unavailable.")
    original_file = (
        target_ref.public_id
        if target_ref.model_label == "storage.File" and target_ref.public_id in identities
        else None
    )

    return canonical_json_sha256(
        {
            "original_source": original_file,
            "target": {
                "model_label": target_ref.model_label,
                "object_id": str(target_ref.object_id),
                "resource_type": target_ref.resource_type,
            },
        }
    )


def _model_fingerprint(model: Any | None) -> dict[str, Any] | None:
    if model is None:
        return None
    provider: Any = model.provider
    return {
        "id": str(model.sqid),
        "deployment": model.deployment_identity(),
        "provider_config": provider.config,
        "model_config": model.config,
    }


def _validate_document_result(
    result: DocumentResult, *, source_count: int, has_model: bool, has_recognition_model: bool
) -> None:
    if not isinstance(result.value, dict):
        raise ValidationError({"result": "Document extraction output root must be an object."})
    _json_object(result.value, field="result")
    _validate_parts(result.parts, source_count=source_count)
    if not isinstance(result.claims, dict):
        raise ValidationError({"result": "Document claims must be an object."})
    _reject_json_nul(result.claims, field="result")
    for pointer, claims in result.claims.items():
        if not isinstance(pointer, str) or not pointer.startswith("/") or not isinstance(claims, list):
            raise ValidationError({"result": "Document claims must use JSON pointers."})
        if any(
            not isinstance(claim, dict)
            or not isinstance(claim.get("part_position"), int)
            or claim["part_position"] < 0
            or claim["part_position"] >= len(result.parts)
            for claim in claims
        ):
            raise ValidationError({"result": "Document claims reference unavailable evidence."})
    _json_object(result.provider_metadata or {}, field="provider_metadata")
    roles = set(result.used_model_roles)
    if not roles <= {"mapping", "recognition"}:
        raise ValidationError({"result": "Document extraction reported an unsupported model role."})
    if ("mapping" in roles and not has_model) or ("recognition" in roles and not has_recognition_model):
        raise ValidationError({"result": "Document extraction used an unconfigured inference model."})


def _validate_parts(parts: Sequence[Any], *, source_count: int) -> None:
    for part in parts:
        if not isinstance(part, DocumentPart):
            raise ValidationError({"result": "Document evidence has an unsupported shape."})
        if part.source_position < 0 or part.source_position >= source_count:
            raise ValidationError({"result": "Document evidence references an unavailable source."})
        if part.kind not in ExtractionPartKind.values:
            raise ValidationError({"result": "Document evidence has an unsupported kind."})
        try:
            json.dumps(part.value, allow_nan=False)
            json.dumps(part.metadata or {}, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValidationError({"result": "Document evidence must contain JSON values."}) from error
        _reject_json_nul(part.value, field="result")
        _reject_json_nul(part.metadata or {}, field="result")


def _validated_schema(schema: Any) -> dict[str, Any]:
    value = _json_object(schema, field="schema")
    try:
        Draft202012Validator.check_schema(value)
    except Exception as error:
        raise ValidationError({"schema": "Extraction schema is not valid JSON Schema."}) from error
    if value.get("type") != "object":
        raise ValidationError({"schema": "Extraction schema root must have type object."})
    return value


def _json_object(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError({field: "Must be a JSON object."})
    _reject_json_nul(value, field=field)
    try:
        return json.loads(json.dumps(dict(value), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValidationError({field: "Must contain JSON-compatible values."}) from error


def _reject_json_nul(value: Any, *, field: str) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise ValidationError({field: "Must not contain null characters."})
    elif isinstance(value, Mapping):
        for key, item in value.items():
            _reject_json_nul(key, field=field)
            _reject_json_nul(item, field=field)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _reject_json_nul(item, field=field)
