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
from angee.workflows_ocr.engines import PageImage, PageResult


def extract(
    *,
    files: Sequence[Any],
    schema: dict[str, Any],
    model: Any,
    authorized_target: Any,
    engine: str = "glm",
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Extract ordered files into immutable evidence scoped to ``authorized_target``.

    The caller must be able to read every source and the target before the
    narrow system write begins. Exact source/model/schema/config/scope repeats
    reuse an existing extraction; any changed fact creates the next revision.
    """

    ordered_files = tuple(files)
    owner_id = actor_user_id(current_actor())
    if not ordered_files:
        raise ValidationError({"files": "At least one source file is required."})
    if len({file.pk for file in ordered_files}) != len(ordered_files):
        raise ValidationError({"files": "Each source file may appear only once in an extraction."})
    _authorize(ordered_files, authorized_target)
    if not model.has_access("read"):
        raise PermissionDenied("Read access to the inference model is required.")
    normalized_schema = _validated_schema(schema)
    normalized_config = _json_object(config or {}, field="config")
    schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
    if not schema_id:
        raise ValidationError({"schema": "Extraction schemas require a stable $id or x-version."})

    source_facts = [
        {"position": position, "file": str(file.sqid), "content_hash": str(file.content_hash)}
        for position, file in enumerate(ordered_files)
    ]
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
    reuse_key = _digest(
        {
            "lineage": lineage_key,
            "schema": normalized_schema,
            "engine": engine,
            "model": str(model.sqid),
            "model_name": str(model.name),
            "provider": str(model.provider_id),
            "provider_url": str(model.provider.base_url),
            "provider_config": model.provider.config,
            "model_config": model.config,
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
    result: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    status = "succeeded"
    error_code = ""
    try:
        pages = _rasterize(ordered_files)
        engine_class = _engine_class(engine)
        engine_impl = engine_class()
        timeout = float(normalized_config.get("timeout") or settings.ANGEE_OCR_TIMEOUT_SECONDS)
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
        result, conflicts = _merge(page_results)
        errors = sorted(
            Draft202012Validator(normalized_schema).iter_errors(result),
            key=lambda error: list(error.path),
        )
        if errors:
            raise ValidationError({"result": "OCR output does not match the declared schema."})
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
            engine_config=normalized_config,
            result=result,
            provenance={
                "source_count": len(ordered_files),
                "page_count": len(pages),
                "conflicts": conflicts,
                "completed_page_count": len(page_results),
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
            source_model(extraction=extraction, file=file, position=position, content_hash=file.content_hash)
            for position, file in enumerate(ordered_files)
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
    return extraction


def reextract(extraction: Any) -> Any:
    """Create a new evidence revision from a retained failed extraction."""

    if extraction.status != "failed":
        raise ValidationError({"extraction": "Only failed extraction evidence can be retried."})
    config = dict(extraction.engine_config)
    config["retry_of_revision"] = extraction.revision
    return extract(
        files=[source.file for source in extraction.sources.select_related("file").order_by("position")],
        schema=extraction.schema,
        model=extraction.model,
        authorized_target=extraction.target,
        engine=str(extraction.engine),
        config=config,
    )


def _authorize(files: Sequence[Any], target: Any) -> None:
    if not target.has_access("read"):
        raise PermissionDenied("Read access to the extraction target is required.")
    if any(not file.has_access("read") for file in files):
        raise PermissionDenied("Read access to every extraction source is required.")


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
    try:
        return json.loads(json.dumps(dict(value), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValidationError({field: "Must contain JSON-compatible values."}) from error


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _engine_class(key: str) -> type[Any]:
    from angee.base.impl import resolve_impl_class

    from angee.workflows_ocr.engines import OcrEngine

    return resolve_impl_class("ANGEE_OCR_ENGINE_CLASSES", key, base_class=OcrEngine)


def _rasterize(files: Sequence[Any]) -> list[PageImage]:
    pages: list[PageImage] = []
    max_bytes = int(settings.ANGEE_OCR_MAX_BYTES)
    max_pages = int(settings.ANGEE_OCR_MAX_PAGES)
    dpi = int(settings.ANGEE_OCR_DPI)
    for source_position, file in enumerate(files):
        if int(file.size_bytes) > max_bytes:
            raise ValidationError({"files": "An extraction source exceeds the configured byte limit."})
        with file.open_stream() as stream:
            content = stream.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise ValidationError({"files": "An extraction source exceeds the configured byte limit."})
        mime = str(getattr(getattr(file, "mime_type", None), "mime_type", ""))
        images = _pdf_images(content, dpi=dpi) if mime == "application/pdf" else [_load_image(content)]
        for source_page, image in enumerate(images):
            if len(pages) >= max_pages:
                raise ValidationError({"files": "The document exceeds the configured page limit."})
            image.thumbnail((int(settings.ANGEE_OCR_MAX_EDGE), int(settings.ANGEE_OCR_MAX_EDGE)))
            output = io.BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=90)
            pages.append(
                PageImage(
                    source_position,
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


def _merge(results: Sequence[PageResult]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    merged: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    for page in results:
        for key, value in page.value.items():
            if value in (None, "", []):
                continue
            if key not in merged:
                merged[key] = value
            elif isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = [*merged[key], *value]
            elif merged[key] != value:
                conflicts.setdefault(key, [merged[key]]).append(value)
    return merged, conflicts
