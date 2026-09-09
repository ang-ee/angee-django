"""Transactional extraction service callable by workflows and domain operations."""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from jsonschema import Draft202012Validator
from PIL import Image
from rebac import current_actor, system_context

from angee.base.actors import actor_user_id
from angee.base.refs import canonical_record_target, record_ref_for
from angee.workflows_ocr.engines import (
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    OcrEngine,
    PageImage,
    PageResult,
)


def extract(
    *,
    files: Sequence[Any],
    schema: dict[str, Any],
    model: Any | None,
    authorized_target: Any,
    message_parts: Sequence[Any] = (),
    recognition_model: Any | None = None,
    engine: str = "glm",
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Extract ordered files into immutable evidence scoped to ``authorized_target``.

    The caller must be able to read every source and the target before the
    narrow system write begins. Exact source/model/schema/config/scope repeats
    reuse an existing extraction; any changed fact creates the next revision.
    """

    ordered_files = tuple(files)
    ordered_message_parts = tuple(message_parts)
    owner_id = actor_user_id(current_actor())
    if not ordered_files and not ordered_message_parts:
        raise ValidationError({"files": "At least one file or message part is required."})
    if len({file.pk for file in ordered_files}) != len(ordered_files):
        raise ValidationError({"files": "Each source file may appear only once in an extraction."})
    if len({part.pk for part in ordered_message_parts}) != len(ordered_message_parts):
        raise ValidationError({"message_parts": "Each message part may appear only once in an extraction."})
    _authorize(ordered_files, ordered_message_parts, authorized_target)
    for candidate in (model, recognition_model):
        if candidate is not None and not candidate.has_access("read"):
            raise PermissionDenied("Read access to every inference model is required.")
    normalized_schema = _validated_schema(schema)
    normalized_config = _json_object(config or {}, field="config")
    schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
    if not schema_id:
        raise ValidationError({"schema": "Extraction schemas require a stable $id or x-version."})

    document_sources = _document_sources(ordered_files, ordered_message_parts)
    source_facts = [_source_fact(source) for source in document_sources]
    target_ref = record_ref_for(authorized_target)
    lineage_key = _digest(
        {
            "sources": source_facts,
            "schema_id": schema_id,
            "target": {
                "model_label": target_ref.model_label,
                "object_id": str(target_ref.object_id),
                "resource_type": target_ref.resource_type,
            },
        }
    )
    engine_class = _engine_class(engine)
    reuse_key = _digest(
        {
            "lineage": lineage_key,
            "schema": normalized_schema,
            "engine": engine,
            "pipeline_version": str(engine_class.pipeline_version),
            "model": _model_fingerprint(model),
            "recognition_model": _model_fingerprint(recognition_model),
            "config": normalized_config,
        }
    )
    extraction_model = apps.get_model("workflows_ocr", "Extraction")
    with system_context(reason="workflows_ocr.extract.reuse"):
        existing = extraction_model._base_manager.filter(reuse_key=reuse_key).first()
    if existing is not None:
        return existing

    pages: list[PageImage] = []
    page_results: list[PageResult] = []
    document_result: DocumentResult | None = None
    document_claims: dict[str, list[dict[str, Any]]] = {}
    document_metadata: dict[str, Any] = {}
    used_model_roles: tuple[str, ...] = ()
    retained_parts = ()
    result: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    status = "succeeded"
    error_code = ""
    try:
        engine_impl = engine_class()
        timeout = float(normalized_config.get("timeout") or settings.ANGEE_OCR_TIMEOUT_SECONDS)
        if type(engine_impl).extract_document is not OcrEngine.extract_document:
            document_result = engine_impl.extract_document(
                document_sources,
                normalized_schema,
                model=model,
                recognition_model=recognition_model,
                config=normalized_config,
                timeout=timeout,
            )
            _validate_document_result(
                document_result,
                source_count=len(document_sources),
                has_model=model is not None,
                has_recognition_model=recognition_model is not None,
            )
            result = document_result.value
            retained_parts = document_result.parts
            document_claims = document_result.claims
            document_metadata = document_result.engine_metadata or {}
            used_model_roles = document_result.used_model_roles
        else:
            if model is None:
                raise ValidationError({"model": "Legacy page extraction requires an inference model."})
            if ordered_message_parts:
                raise ValidationError({"message_parts": "The selected legacy page engine accepts files only."})
            pages = _rasterize(document_sources)
            started = time.monotonic()
            for page in pages:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Document extraction exceeded its configured timeout.")
                page_results.append(
                    engine_impl.extract_page(
                        page,
                        normalized_schema,
                        model=model,
                        config=normalized_config,
                        timeout=remaining,
                    )
                )
            result, conflicts = _merge(page_results, schema=normalized_schema)
        errors = sorted(
            Draft202012Validator(normalized_schema).iter_errors(result),
            key=lambda error: list(error.path),
        )
        if errors:
            raise ValidationError({"result": "OCR output does not match the declared schema."})
    except DocumentPipelineError as error:
        retained_parts = error.parts
        _validate_parts(retained_parts, source_count=len(source_facts))
        status = "failed"
        error_code = type(error).__name__
    except (RuntimeError, TimeoutError, ValidationError) as error:
        # The retained code is actionable without copying document values or a
        # provider response into an exception or workflow journal.
        status = "failed"
        error_code = type(error).__name__

    target = canonical_record_target(authorized_target)
    with transaction.atomic(), system_context(reason="workflows_ocr.extract.persist"):
        extraction = extraction_model(
            revision=1,
            lineage_key=lineage_key,
            reuse_key=reuse_key,
            status=status,
            error_code=error_code,
            schema_id=schema_id,
            schema=normalized_schema,
            schema_digest=_digest(normalized_schema),
            engine=engine,
            model=model,
            recognition_model=recognition_model,
            engine_config=normalized_config,
            result=result,
            provenance={
                "source_count": len(source_facts),
                "page_count": len(pages) or sum(part.source_page is not None for part in retained_parts),
                "conflicts": conflicts,
                "completed_page_count": len(page_results)
                or sum(part.source_page is not None for part in retained_parts),
                "claims": document_claims,
                "document": document_metadata,
                "configured_model_roles": [
                    role
                    for role, configured in (
                        ("mapping", model is not None),
                        ("recognition", recognition_model is not None),
                    )
                    if configured
                ],
                "used_model_roles": list(used_model_roles),
                "target": {"resource_type": target_ref.resource_type, "public_id": target_ref.public_id},
            },
            content_type=target.content_type,
            object_id=target.object_id,
            created_by_id=owner_id,
        )
        extraction.sudo(reason="workflows_ocr.extract.persist")
        for _attempt in range(3):
            previous = (
                extraction_model._base_manager.select_for_update()
                .filter(lineage_key=lineage_key)
                .order_by("-revision")
                .first()
            )
            extraction.revision = (previous.revision + 1) if previous else 1
            try:
                with transaction.atomic():
                    extraction.save()
                break
            except IntegrityError:
                duplicate = extraction_model._base_manager.filter(reuse_key=reuse_key).first()
                if duplicate is not None:
                    return duplicate
                extraction.pk = None
        else:
            raise IntegrityError("Could not allocate an extraction revision after concurrent writes.")
        source_model = apps.get_model("workflows_ocr", "ExtractionSource")
        page_model = apps.get_model("workflows_ocr", "ExtractionPage")
        sources = [
            source_model(
                extraction=extraction,
                file=source.file,
                message_part=source.message_part,
                position=source.source_position,
                content_hash=source.content_hash,
            )
            for source in document_sources
        ]
        source_model._base_manager.bulk_create(sources)
        page_model._base_manager.bulk_create(
            [
                page_model(
                    extraction=extraction,
                    source=sources[page.source_position],
                    position=position,
                    source_page=page.page_position,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    duration_ms=max(page_results[position].duration_ms, 0),
                    result=page_results[position].value,
                    engine_metadata=page_results[position].engine_metadata or {},
                )
                for position, page in enumerate(pages[: len(page_results)])
            ]
        )
        if retained_parts:
            part_model = apps.get_model("workflows_ocr", "ExtractionPart")
            part_model._base_manager.bulk_create(
                [
                    part_model(
                        extraction=extraction,
                        source=sources[part.source_position],
                        position=position,
                        source_page=part.source_page,
                        mime_type=part.mime_type,
                        kind=part.kind,
                        method=part.method,
                        content_hash=part.content_hash,
                        width=part.width,
                        height=part.height,
                        dpi=part.dpi,
                        value=part.value,
                        claims=_claims_for_part(document_claims, position),
                        metadata=part.metadata or {},
                        duration_ms=max(part.duration_ms, 0),
                    )
                    for position, part in enumerate(retained_parts)
                ]
            )
    return extraction


def reextract(extraction: Any) -> Any:
    """Create a new evidence revision from a retained failed extraction."""

    if extraction.status != "failed":
        raise ValidationError({"extraction": "Only failed extraction evidence can be retried."})
    config = dict(extraction.engine_config)
    config["retry_of_revision"] = extraction.revision
    sources = list(
        extraction.sources.select_related("file", "message_part__fragment", "message_part__message").order_by(
            "position"
        )
    )
    return extract(
        files=[source.file for source in sources if source.file_id is not None],
        message_parts=[source.message_part for source in sources if source.message_part_id is not None],
        schema=extraction.schema,
        model=extraction.model,
        recognition_model=extraction.recognition_model,
        authorized_target=extraction.target,
        engine=str(extraction.engine),
        config=config,
    )


def _authorize(files: Sequence[Any], message_parts: Sequence[Any], target: Any) -> None:
    if not target.has_access("read"):
        raise PermissionDenied("Read access to the extraction target is required.")
    if any(not file.has_access("read") for file in files):
        raise PermissionDenied("Read access to every extraction source is required.")
    if any(not part.has_access("read") for part in message_parts):
        raise PermissionDenied("Read access to every extraction source is required.")


def _message_part_hash(part: Any) -> str:
    if part.fragment_id is None:
        raise ValidationError({"message_parts": "Text message parts require a retained fragment."})
    if not str(part.type).startswith("text/"):
        raise ValidationError({"message_parts": "Only textual message parts can be extraction sources."})
    if str(part.role) not in {"body", "title", "header"}:
        raise ValidationError({"message_parts": "Quoted and signature message parts cannot be extraction sources."})
    if len(part.fragment.text.encode()) > int(settings.ANGEE_OCR_MAX_BYTES):
        raise ValidationError({"message_parts": "An extraction source exceeds the configured byte limit."})
    return str(part.fragment.hash)


def _document_sources(files: Sequence[Any], message_parts: Sequence[Any]) -> tuple[DocumentSource, ...]:
    values = []
    for i, file in enumerate(files):
        with file.open_stream() as stream:
            content = stream.read(int(settings.ANGEE_OCR_MAX_BYTES) + 1)
        if len(content) > int(settings.ANGEE_OCR_MAX_BYTES):
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
    identity = (
        {"file": str(source.file.sqid)} if source.file is not None else {"message_part": str(source.message_part.sqid)}
    )
    return {"position": source.source_position, **identity, "content_hash": source.content_hash}


def _model_fingerprint(model: Any | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "id": str(model.sqid),
        "name": str(model.name),
        "provider": str(model.provider_id),
        "provider_url": str(model.provider.base_url),
        "provider_config": model.provider.config,
        "model_config": model.config,
    }


def _claims_for_part(claims: Mapping[str, list[dict[str, Any]]], position: int) -> dict[str, Any]:
    return {
        pointer: [claim for claim in entries if claim.get("part_position") == position]
        for pointer, entries in claims.items()
        if any(claim.get("part_position") == position for claim in entries)
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
    _json_object(result.engine_metadata or {}, field="engine_metadata")
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
        if part.kind not in {"structured", "native_text", "recognized_text"}:
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


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _engine_class(key: str) -> type[Any]:
    from angee.base.impl import resolve_impl_class
    from angee.workflows_ocr.engines import OcrEngine

    return resolve_impl_class("ANGEE_OCR_ENGINE_CLASSES", key, base_class=OcrEngine)


def _rasterize(sources: Sequence[DocumentSource]) -> list[PageImage]:
    pages: list[PageImage] = []
    max_pages = int(settings.ANGEE_OCR_MAX_PAGES)
    dpi = int(settings.ANGEE_OCR_DPI)
    for source in sources:
        if source.file is None or not isinstance(source.content, bytes):
            raise ValidationError({"files": "Legacy page extraction accepts stored files only."})
        content = source.content
        mime = source.mime_type
        images = _pdf_images(content, dpi=dpi) if mime == "application/pdf" else [_load_image(content)]
        for source_page, image in enumerate(images):
            if len(pages) >= max_pages:
                raise ValidationError({"files": "The document exceeds the configured page limit."})
            image.thumbnail((int(settings.ANGEE_OCR_MAX_EDGE), int(settings.ANGEE_OCR_MAX_EDGE)))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=90)
            pages.append(
                PageImage(
                    source.source_position,
                    source_page,
                    "image/jpeg",
                    output.getvalue(),
                    image.width,
                    image.height,
                    dpi,
                )
            )
    return pages


def _load_image(content: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
        return image
    except Exception as error:
        raise ValidationError({"files": "Extraction sources must be PDF or supported image files."}) from error


def _pdf_images(content: bytes, *, dpi: int) -> list[Image.Image]:
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(content)
        scale = dpi / 72
        return [page.render(scale=scale).to_pil() for page in document]
    except Exception as error:
        raise ValidationError({"files": "The PDF could not be rasterized."}) from error


def _merge(
    results: Sequence[PageResult],
    *,
    schema: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """Merge page objects while retaining required, explicitly nullable facts.

    Empty values normally contribute no evidence.  A required property whose
    schema accepts JSON null is different: an explicit null is the model's
    evidence that the field was inspected and absent, and must survive when no
    page supplies a substantive value.
    """

    merged: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    required = set(schema.get("required", ())) if isinstance(schema, Mapping) else set()
    properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    required_nullable = {
        key
        for key in required
        if isinstance(properties, Mapping)
        and isinstance(properties.get(key), Mapping)
        and Draft202012Validator(dict(properties[key])).is_valid(None)
    }
    for page in results:
        for key, value in page.value.items():
            if value is None:
                if key in required_nullable and key not in merged:
                    merged[key] = None
                continue
            if value in ("", []):
                continue
            if key not in merged:
                merged[key] = value
            elif merged[key] is None:
                merged[key] = value
            elif isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = [*merged[key], *value]
            elif merged[key] != value:
                conflicts.setdefault(key, [merged[key]]).append(value)
    return merged, conflicts
