"""Transactional extraction service callable by workflows and domain operations."""

from __future__ import annotations

import hashlib
import io
import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

import pypdfium2 as pdfium
from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from jsonschema import Draft202012Validator
from PIL import Image
from rebac import current_actor, system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.impl import resolve_impl_class
from angee.base.refs import RecordRef, canonical_record_target, record_ref_for
from angee.workflows.attempts import json_values_equal
from angee.workflows_extraction.engines import (
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
    engine: str,
    config: Mapping[str, Any] | None = None,
) -> Any:
    """Extract ordered files into immutable evidence scoped to ``authorized_target``.

    The caller must be able to read every source and the target before the
    narrow system write begins. Exact source/model/schema/config/scope repeats
    reuse an existing extraction; any changed fact creates the next revision.
    """

    ordered_files = tuple(files)
    ordered_message_parts = tuple(message_parts)
    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    owner_id = actor_user_id(actor)
    if not ordered_files and not ordered_message_parts:
        raise ValidationError({"files": "At least one file or message part is required."})
    if len({file.pk for file in ordered_files}) != len(ordered_files):
        raise ValidationError({"files": "Each source file may appear only once in an extraction."})
    if len({part.pk for part in ordered_message_parts}) != len(ordered_message_parts):
        raise ValidationError({"message_parts": "Each message part may appear only once in an extraction."})
    _authorize(ordered_files, ordered_message_parts, authorized_target, actor=actor)
    for candidate in (model, recognition_model):
        if candidate is not None and not candidate.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to every inference model is required.")
    require_approved_model_deployment(model, role="mapping")
    require_approved_model_deployment(recognition_model, role="recognition")
    normalized_schema = _validated_schema(schema)
    normalized_config = _json_object(config or {}, field="config")
    schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
    if not schema_id:
        raise ValidationError({"schema": "Extraction schemas require a stable $id or x-version."})

    document_sources = _document_sources(ordered_files, ordered_message_parts)
    source_facts = [_source_fact(source) for source in document_sources]
    target_ref = record_ref_for(authorized_target)
    lineage_key = _lineage_key(source_facts=source_facts, target_ref=target_ref)
    engine_class = _engine_class(engine)
    reuse_key = _digest(
        {
            "lineage": lineage_key,
            "source_facts": source_facts,
            "schema": normalized_schema,
            "engine": engine,
            "pipeline_version": str(engine_class.pipeline_version),
            "model": _model_fingerprint(model),
            "recognition_model": _model_fingerprint(recognition_model),
            "config": normalized_config,
        }
    )
    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    with system_context(reason="workflows_extraction.extract.reuse"):
        existing = extraction_model._base_manager.filter(reuse_key=reuse_key).first()
        base = extraction_model._base_manager.filter(lineage_key=lineage_key).order_by("-revision").first()
    if existing is not None:
        return existing

    pages: list[PageImage] = []
    page_results: list[PageResult] = []
    document_result: DocumentResult | None = None
    document_claims: dict[str, list[dict[str, Any]]] = {}
    document_metadata: dict[str, Any] = {}
    used_model_roles: tuple[str, ...] = ()
    retained_parts: tuple[DocumentPart, ...] = ()
    result: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    status = "succeeded"
    error_code = ""
    try:
        engine_impl = engine_class()
        timeout = float(normalized_config.get("timeout") or settings.ANGEE_OCR_TIMEOUT_SECONDS)
        if engine_impl.document_engine:
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
            document_metadata = dict(document_result.engine_metadata or {})
            document_metadata["pipeline_duration_ms"] = document_result.duration_ms
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
        error_code = ":".join(value for value in (error.stage, error.code) if value) or type(error).__name__
        document_metadata = {
            **error.metadata,
            "failure": {
                "stage": error.stage or "document_pipeline",
                "code": error.code or type(error).__name__,
            },
        }
    except (RuntimeError, TimeoutError, ValidationError) as error:
        # The retained code is actionable without copying document values or a
        # provider response into an exception or workflow journal.
        status = "failed"
        stage = "result_validation" if isinstance(error, ValidationError) else "document_pipeline"
        code = type(error).__name__
        error_code = f"{stage}:{code}"
        document_metadata = {**document_metadata, "failure": {"stage": stage, "code": code}}

    target = canonical_record_target(authorized_target)
    return extraction_model.objects.create_revision(
        sources=document_sources,
        pages=pages,
        page_results=page_results,
        parts=retained_parts,
        lineage_key=lineage_key,
        reuse_key=reuse_key,
        expected_base_id=base.pk if base is not None else None,
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
            "completed_page_count": len(page_results) or sum(part.source_page is not None for part in retained_parts),
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


def reextract(extraction: Any) -> Any:
    """Return the newest compatible success or retry its newest failure."""

    if extraction.status != "failed":
        raise ValidationError({"extraction": "Only failed extraction evidence can be retried."})
    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    with system_context(reason="workflows_extraction.reextract.latest"):
        candidates = extraction_model._base_manager.filter(
            lineage_key=extraction.lineage_key,
        ).order_by("-revision")
        latest = next(
            (
                candidate
                for candidate in candidates
                if _same_retry_policy(candidate, extraction)
            ),
            None,
        )
    if latest is None:
        raise ValidationError({"extraction": "The retained extraction lineage is unavailable."})
    sources = list(
        latest.sources.select_related("file", "message_part__fragment", "message_part__message").order_by(
            "position"
        )
    )
    files = [source.file for source in sources if source.file_id is not None]
    message_parts = [source.message_part for source in sources if source.message_part_id is not None]
    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    _authorize(files, message_parts, latest.target, actor=actor)
    for candidate in (latest.model, latest.recognition_model):
        if candidate is not None and not candidate.with_actor(actor).has_access("read"):
            raise PermissionDenied("Read access to every inference model is required.")
    if latest.status == "succeeded":
        return latest
    config = authored_engine_config(latest.engine_config)
    config["retry_of_revision"] = latest.revision
    return extract(
        files=files,
        message_parts=message_parts,
        schema=latest.schema,
        model=latest.model,
        recognition_model=latest.recognition_model,
        authorized_target=latest.target,
        engine=str(latest.engine),
        config=config,
    )


def revise(
    extraction: Any, *, result: Mapping[str, Any], decision: Any,
    identity_mapping: Mapping[str, str] | None = None,
    retired_identities: Mapping[str, str] | None = None,
) -> Any:
    """Retain a schema-valid human correction as a new evidence revision.

    The actor must be able to read the original extraction, its target and
    sources, and a completed Decision whose payload names the exact extraction
    id and revision. The Decision authorizes this generic evidence operation
    only; its domain action, resolution payload, and permitted corrected fields
    remain the caller's responsibility.

    The new revision clones retained source, page, and part evidence without
    reacquiring sources or invoking an engine. Claims survive only where their
    JSON-pointer value and every containing array element are unchanged. The
    original revision remains immutable; exact retries reuse one result while
    stale or competing corrections fail.
    """

    actor = current_actor()
    if actor is None:
        raise PermissionDenied("Authentication required.")
    owner_id = actor_user_id(actor)
    extraction_model = apps.get_model("workflows_extraction", "Extraction")
    decision_model = apps.get_model("workflows", "Decision")
    if not isinstance(extraction, extraction_model) or extraction.pk is None:
        raise ValidationError({"extraction": "A retained extraction is required."})
    if not isinstance(decision, decision_model) or decision.pk is None:
        raise ValidationError({"decision": "A retained Decision is required."})
    with system_context(reason="workflows_extraction.revise.load_authority"):
        original = extraction_model._base_manager.filter(pk=extraction.pk).first()
        authority = decision_model._base_manager.filter(pk=decision.pk).first()
        if original is None:
            raise ValidationError({"extraction": "The retained extraction is unavailable."})
        if authority is None:
            raise ValidationError({"decision": "The retained Decision is unavailable."})
        target = original.target
        if target is None:
            raise ValidationError({"extraction": "The retained extraction target is unavailable."})
        retained_sources = list(
            original.sources.select_related(
                "file", "message_part__fragment", "message_part__message"
            ).order_by("position")
        )

    if not original.with_actor(actor).has_access("read"):
        raise PermissionDenied("Read access to the extraction is required.")
    files = [source.file for source in retained_sources if source.file_id is not None]
    message_parts = [
        source.message_part for source in retained_sources if source.message_part_id is not None
    ]
    _authorize(files, message_parts, target, actor=actor)
    if not authority.with_actor(actor).has_access("read"):
        raise PermissionDenied("Read access to the correction Decision is required.")
    if str(authority.verdict) != "completed":
        raise ValidationError({"decision": "The correction Decision must be completed."})

    original_ref = record_ref_for(original)
    decision_ref = record_ref_for(authority)
    _validate_correction_binding(authority.payload, extraction_ref=original_ref, revision=original.revision)
    normalized_schema = _validated_schema(original.schema)
    schema_id = str(normalized_schema.get("$id") or normalized_schema.get("x-version") or "")
    if (
        not schema_id
        or schema_id != str(original.schema_id)
        or _digest(normalized_schema) != str(original.schema_digest)
    ):
        raise ValidationError({"extraction": "The retained extraction schema identity is invalid."})
    normalized_result = _json_object(result, field="result")
    errors = sorted(
        Draft202012Validator(normalized_schema).iter_errors(normalized_result),
        key=lambda error: list(error.path),
    )
    if errors:
        raise ValidationError({"result": "Corrected output does not match the retained extraction schema."})

    source_facts = _retained_source_facts(retained_sources)
    target_ref = record_ref_for(target)
    lineage_key = _lineage_key(source_facts=source_facts, target_ref=target_ref)
    if lineage_key != str(original.lineage_key):
        raise ValidationError({"extraction": "The retained extraction source identity is invalid."})

    original_provenance = _json_object(original.provenance, field="extraction")
    claims = _unchanged_claims(
        original_provenance.get("claims", {}),
        before=original.result,
        after=normalized_result,
        original_refs=original.document_refs,
        identity_mapping=identity_mapping or {},
    )
    corrections = original_provenance.get("corrections", [])
    if not isinstance(corrections, list) or not all(isinstance(entry, Mapping) for entry in corrections):
        raise ValidationError({"extraction": "The retained correction provenance is invalid."})
    correction = {
        "kind": "human_correction",
        "original_extraction_id": original_ref.public_id,
        "original_extraction_revision": original.revision,
        "decision_id": decision_ref.public_id,
        "decision_resolved_by": str(authority.resolved_by),
        "recorded_by": str(to_subject_ref(actor)),
        "corrected_paths": list(_changed_json_pointers(original.result, normalized_result)),
        "result_digest": _digest(normalized_result),
    }
    provenance = {
        **original_provenance,
        "claims": claims,
        "used_model_roles": [],
        "corrections": [*corrections, correction],
    }
    reuse_key = _digest(
        {
            "human_correction": {
                "extraction_id": original_ref.public_id,
                "extraction_revision": original.revision,
                "decision_id": decision_ref.public_id,
            }
        }
    )
    return extraction_model.objects.create_revision_from_evidence(
        original,
        lineage_key=lineage_key,
        reuse_key=reuse_key,
        expected_base_id=original.pk,
        identity_mapping=dict(identity_mapping or {}),
        retired_identities=dict(retired_identities or {}),
        status="succeeded",
        error_code="",
        schema_id=schema_id,
        schema=normalized_schema,
        schema_digest=str(original.schema_digest),
        engine=str(original.engine),
        model=original.model,
        recognition_model=original.recognition_model,
        engine_config=original.engine_config,
        result=normalized_result,
        provenance=provenance,
        content_type_id=original.content_type_id,
        object_id=original.object_id,
        created_by_id=owner_id,
    )


def _validate_correction_binding(payload: Any, *, extraction_ref: Any, revision: int) -> None:
    if not isinstance(payload, Mapping):
        raise ValidationError({"decision": "The correction Decision payload must be an object."})
    if type(payload.get("extraction_id")) is not str or payload["extraction_id"] != extraction_ref.public_id:
        raise ValidationError({"decision": "The correction Decision names a different extraction."})
    bound_revision = payload.get("extraction_revision")
    if type(bound_revision) is not int or bound_revision != revision:
        raise ValidationError({"decision": "The correction Decision names a different extraction revision."})


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
    claims: Any, *, before: Any, after: Any,
    original_refs: Any = (), identity_mapping: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Move equal claims only where reviewed logical correspondence proves identity."""

    if not isinstance(claims, Mapping):
        raise ValidationError({"extraction": "The retained extraction claims are invalid."})
    retained: dict[str, Any] = {}
    old_selectors = {
        identity: selector
        for ref in original_refs
        for selector, identity in (
            (ref.selector, ref.identity),
            *((line.selector, line.identity) for line in ref.lines),
        )
    }
    new_selectors = {identity: selector for selector, identity in (identity_mapping or {}).items()
                     if identity != "new"}
    for pointer, entries in claims.items():
        if not isinstance(pointer, str) or not pointer.startswith("/") or not isinstance(entries, list):
            raise ValidationError({"extraction": "The retained extraction claims are invalid."})
        if any(
            not isinstance(entry, Mapping)
            or type(entry.get("part_position")) is not int
            or entry["part_position"] < 0
            for entry in entries
        ):
            raise ValidationError({"extraction": "The retained extraction claims are invalid."})
        old_value = _json_pointer_value(before, pointer)
        mapped_pointer = pointer
        matched = sorted(
            ((selector, identity) for identity, selector in old_selectors.items()
             if selector and (pointer == selector or pointer.startswith(f"{selector}/"))),
            key=lambda item: len(item[0]), reverse=True,
        )
        if matched and matched[0][1] in new_selectors:
            selector, identity = matched[0]
            mapped_pointer = new_selectors[identity] + pointer[len(selector):]
        elif matched and matched[0][1] not in new_selectors:
            continue
        new_value = _json_pointer_value(
            after, mapped_pointer,
            array_element_baseline=before if mapped_pointer == pointer and not matched else _MISSING,
        )
        if (
            old_value is not _MISSING
            and new_value is not _MISSING
            and json_values_equal(old_value, new_value)
        ):
            retained[mapped_pointer] = entries
    return _json_object(retained, field="extraction")


_MISSING = object()


def json_pointer_value(value: Any, pointer: str) -> Any:
    """Resolve one RFC 6901 pointer or raise ``KeyError`` when it is invalid or absent.

    The public raising contract is consumed by downstream accounting-intake steps;
    :func:`_json_pointer_value` keeps the internal sentinel/array-baseline shape.
    """

    if pointer == "":
        return value
    resolved = _json_pointer_value(value, pointer)
    if resolved is _MISSING:
        raise KeyError(pointer)
    return resolved


def _json_pointer_value(
    value: Any, pointer: str, *, array_element_baseline: Any = _MISSING,
) -> Any:
    current = value
    baseline = array_element_baseline
    for encoded in pointer.removeprefix("/").split("/"):
        if "~" in encoded:
            index = 0
            while (index := encoded.find("~", index)) >= 0:
                if index + 1 >= len(encoded) or encoded[index + 1] not in {"0", "1"}:
                    return _MISSING
                index += 2
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                return _MISSING
            current = current[token]
            if baseline is not _MISSING:
                if not isinstance(baseline, Mapping) or token not in baseline:
                    return _MISSING
                baseline = baseline[token]
        elif isinstance(current, list):
            if not token.isascii() or not token.isdigit() or (token.startswith("0") and token != "0"):
                return _MISSING
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
            if baseline is not _MISSING:
                if not isinstance(baseline, list) or index >= len(baseline):
                    return _MISSING
                baseline = baseline[index]
                if not json_values_equal(current, baseline):
                    return _MISSING
        else:
            return _MISSING
    return current


def _changed_json_pointers(before: Any, after: Any, pointer: str = "") -> tuple[str, ...]:
    if json_values_equal(before, after):
        return ()
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        changed: list[str] = []
        for key in sorted(set(before) | set(after)):
            child = f"{pointer}/{str(key).replace('~', '~0').replace('/', '~1')}"
            if key not in before or key not in after:
                changed.append(child)
            else:
                changed.extend(_changed_json_pointers(before[key], after[key], child))
        return tuple(changed)
    if isinstance(before, list) and isinstance(after, list):
        changed = []
        for index in range(max(len(before), len(after))):
            child = f"{pointer}/{index}"
            if index >= len(before) or index >= len(after):
                changed.append(child)
            else:
                changed.extend(_changed_json_pointers(before[index], after[index], child))
        return tuple(changed)
    return (pointer,)


def authored_engine_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Return authored engine policy without native retry lineage metadata."""

    return {key: value for key, value in dict(config).items() if key != "retry_of_revision"}


def _same_retry_policy(candidate: Any, original: Any) -> bool:
    return (
        str(candidate.engine) == str(original.engine)
        and candidate.model_id == original.model_id
        and candidate.recognition_model_id == original.recognition_model_id
        and str(candidate.schema_digest) == str(original.schema_digest)
        and authored_engine_config(candidate.engine_config) == authored_engine_config(original.engine_config)
    )


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
    if source.file is not None:
        identity = {"file": str(source.file.sqid)}
    elif source.message_part is not None:
        identity = {"message_part": str(source.message_part.sqid)}
    else:
        raise ValueError("Document source identity unavailable.")
    return {"position": source.source_position, **identity, "content_hash": source.content_hash}


def _lineage_key(
    *, source_facts: Sequence[Mapping[str, Any]], target_ref: RecordRef
) -> str:
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

    return _digest(
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
    return {
        "id": str(model.sqid),
        "name": str(model.name),
        "provider": str(model.provider_id),
        "provider_url": str(model.provider.base_url),
        "provider_config": model.provider.config,
        "model_config": model.config,
    }


def model_deployment_identity(model: Any) -> dict[str, str]:
    """Return the non-secret endpoint binding checked before model invocation."""

    provider = model.provider
    backend = provider.backend
    effective_url = str(provider.base_url or getattr(backend, "default_base_url", "")).strip().rstrip("/")
    return {
        "model": str(model.sqid),
        "provider": str(provider.sqid),
        "backend": str(provider.backend_class),
        "native_model": str(model.provider_model_name),
        "endpoint": effective_url,
    }


def require_approved_model_deployment(model: Any | None, *, role: str) -> None:
    """Fail closed when a configured OCR deployment allowlist excludes a model."""

    try:
        validate_model_deployment(model, role=role)
    except ValueError as error:
        raise PermissionDenied(str(error)) from error


def validate_model_deployment(model: Any | None, *, role: str) -> None:
    """Validate one configured model against the shared deployment allowlist."""

    if model is None:
        return
    policy = getattr(settings, "ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS", None)
    if policy is None:
        return
    if not isinstance(policy, Mapping):
        raise ValueError("The OCR model deployment policy is invalid.")
    approved = policy.get(role)
    if not isinstance(approved, (list, tuple)) or not all(isinstance(item, Mapping) for item in approved):
        raise ValueError(f"The OCR {role} deployment policy is invalid.")
    identity = model_deployment_identity(model)
    if not any(dict(item) == identity for item in approved):
        raise ValueError(f"The configured OCR {role} model deployment is not approved.")


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
    """Merge page evidence, preserving schema-valid required empty values.

    An explicitly empty required collection or nullable field is still a claim.
    Later substantive evidence replaces it; absent optional values add no claim.
    """

    merged: dict[str, Any] = {}
    conflicts: dict[str, list[Any]] = {}
    required = set(schema.get("required", ())) if isinstance(schema, Mapping) else set()
    properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    validator = Draft202012Validator(schema or {})
    for page in results:
        for key, value in page.value.items():
            if value in (None, "", []):
                if (
                    key in required
                    and key not in merged
                    and validator.evolve(schema=properties.get(key, {})).is_valid(value)
                ):
                    merged[key] = value
                continue
            if key not in merged:
                merged[key] = value
            elif merged[key] in (None, ""):
                merged[key] = value
            elif isinstance(merged[key], list) and isinstance(value, list):
                merged[key] = [*merged[key], *value]
            elif merged[key] != value:
                conflicts.setdefault(key, [merged[key]]).append(value)
    return merged, conflicts
