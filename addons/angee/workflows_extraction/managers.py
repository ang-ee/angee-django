"""Append-only evidence collections and atomic revision allocation."""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError, transaction
from rebac import system_context

from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.authority import TransactionBoundAuthority
from angee.workflows.attempts import json_values_equal
from angee.workflows_extraction.engines import DocumentPart, DocumentSource, PageImage, PageResult


_evidence_insertion = TransactionBoundAuthority[None](
    "extraction_retention_transaction",
    atomic_error="Extraction evidence insertion requires an active transaction.",
    nested_error="Extraction evidence insertion already has an owner.",
)


def evidence_insert_allowed(alias: str | None = None) -> bool:
    return _evidence_insertion.is_active(alias or DEFAULT_DB_ALIAS)


class ImmutableEvidenceQuerySet(AngeeQuerySet[Any]):
    """Prevent post-insert mutation and deletion through bulk ORM paths."""

    def update(self, **kwargs: Any) -> int:
        raise ValueError("Extraction evidence is immutable.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction evidence is retained and cannot be deleted through the ORM.")

    def _raw_delete(self, using: str) -> int:
        raise ValueError("Extraction evidence cannot be deleted through relation cascades.")

    def create(self, **kwargs: Any) -> Any:
        if not evidence_insert_allowed(self.db):
            raise ValueError("Extraction evidence can only be inserted by the retention owner.")
        return super().create(**kwargs)

    def bulk_create(self, objs: Any, **kwargs: Any) -> Any:
        if not evidence_insert_allowed(self.db) or kwargs.get("update_conflicts") or kwargs.get("ignore_conflicts"):
            raise ValueError("Extraction evidence can only be inserted by the retention owner.")
        return super().bulk_create(objs, **kwargs)

    def bulk_update(self, objs: Any, fields: Any, **kwargs: Any) -> Any:
        raise ValueError("Extraction evidence is immutable.")


ImmutableEvidenceManager: Any = AngeeManager.from_queryset(ImmutableEvidenceQuerySet)


class ExtractionManager(ImmutableEvidenceManager):
    """Persist one authorized result and all of its ordered evidence atomically."""

    def create_revision(
        self,
        *,
        sources: Sequence[DocumentSource],
        pages: Sequence[PageImage],
        page_results: Sequence[PageResult],
        parts: Sequence[DocumentPart],
        **values: Any,
    ) -> Any:
        """Reuse exact requests or allocate the next revision with fresh engine evidence."""

        return self._create_revision(values=values, evidence=(sources, pages, page_results, parts))

    def create_revision_from_evidence(self, original: Any, **values: Any) -> Any:
        """Allocate a revision by cloning an authorized immutable evidence snapshot.

        ``original`` must still be the lineage head. Exact retries reuse the
        correction already allocated for the same authority key; a different
        result for that key is rejected rather than branching retained facts.
        """

        return self._create_revision(values=values, original=original)

    def _create_revision(
        self,
        *,
        values: dict[str, Any],
        evidence: tuple[
            Sequence[DocumentSource],
            Sequence[PageImage],
            Sequence[PageResult],
            Sequence[DocumentPart],
        ] | None = None,
        original: Any | None = None,
    ) -> Any:
        """Own revision locking, retry, reuse, and child persistence once."""

        if (evidence is None) == (original is None):
            raise TypeError("Provide either fresh evidence or one retained extraction.")
        expected_base_id = values.pop("expected_base_id", None)
        identity_mapping = values.pop("identity_mapping", None)
        retired_identities = values.pop("retired_identities", None)
        if not isinstance(values.get("provenance"), dict):
            raise ValidationError({"extraction": "Retention provenance must be an object."})
        values["provenance"] = {
            **values["provenance"],
            "identity_correspondence": {
                "expected_base_id": expected_base_id,
                "continuing_or_new": dict(identity_mapping or {}),
                "retired": dict(retired_identities or {}),
            },
        }
        with system_context(reason="workflows_extraction.extraction.create_revision"):
            for attempt in range(3):
                try:
                    with transaction.atomic(using=self.db), _evidence_insertion.scope(self.db, None):
                        lineage_model = self.model._meta.apps.get_model("workflows_extraction", "ExtractionLineage")
                        lineage, _ = lineage_model._base_manager.using(self.db).get_or_create(key=values["lineage_key"])
                        lineage = lineage_model._base_manager.using(self.db).select_for_update().get(pk=lineage.pk)
                        previous = lineage.head
                        existing = self.filter(reuse_key=values["reuse_key"]).first()
                        unresolved_failure = (
                            values.get("status") == "failed" and values.get("result") == {}
                            and (
                                previous is not None if existing is None else
                                "last_known_revision" in existing.provenance.get("identity_correspondence", {})
                            )
                        )
                        if unresolved_failure:
                            if identity_mapping or retired_identities:
                                raise ValidationError({
                                    "extraction": "A transient source failure cannot remap or retire known identities."
                                })
                            values["provenance"]["identity_correspondence"]["last_known_revision"] = (
                                existing.provenance.get("identity_correspondence", {}).get("last_known_revision")
                                if existing is not None else previous.revision
                            )
                        if existing is not None:
                            return self._validated_reuse(existing, original=original, values=values, evidence=evidence)
                        if (previous.pk if previous is not None else None) != expected_base_id:
                            raise ValidationError({"extraction": "The request's extraction base is no longer current."})
                        if original is not None and (previous is None or previous.pk != original.pk):
                            raise ValidationError({
                                "extraction": "The correction source is no longer the current extraction revision."
                            })
                        if unresolved_failure:
                            document_map = deepcopy(previous.document_map)
                            retired = deepcopy(previous.retired_identities)
                        else:
                            document_map, retired = _document_mapping(
                                values["result"], layout=values["engine_config"].get("evidence_layout", {}),
                                original=previous, identity_mapping=identity_mapping,
                                retired_identities=retired_identities,
                            )
                        extraction = self.create(
                            revision=previous.revision + 1 if previous else 1,
                            document_map=document_map, retired_identities=retired, **values,
                        )
                        if evidence is not None:
                            self._persist_fresh_evidence(extraction, *evidence)
                        else:
                            self._clone_retained_evidence(extraction, original)
                        lineage.head = extraction
                        lineage.save(update_fields=("head",))
                        return extraction
                except IntegrityError:
                    duplicate = self.filter(reuse_key=values["reuse_key"]).first()
                    if duplicate is not None:
                        return self._validated_reuse(duplicate, original=original, values=values, evidence=evidence)
                    if attempt == 2:
                        raise
        raise AssertionError("Unreachable revision allocation state.")

    def _validated_reuse(
        self, existing: Any, *, original: Any | None, values: dict[str, Any], evidence: Any,
    ) -> Any:
        if (
            existing.lineage_key != values["lineage_key"]
            or any(
                not _field_equal(existing, field, requested)
                for field, requested in values.items()
                if field not in {"created_by_id", "provenance"}
            )
            or existing.schema_digest != values["schema_digest"]
            or not json_values_equal(existing.result, values["result"])
            or not json_values_equal(
                _stable_correction_provenance(existing.provenance),
                _stable_correction_provenance(values["provenance"]),
            )
            or (original is not None and existing.revision != original.revision + 1)
        ):
            raise ValidationError({"extraction": "This request identity already owns different retained facts."})
        if evidence is not None and not self._same_fresh_evidence(existing, evidence):
            raise ValidationError({"extraction": "This request identity already owns different source evidence."})
        return existing

    def _same_fresh_evidence(self, existing: Any, evidence: Any) -> bool:
        sources, pages, page_results, parts = evidence
        retained = list(existing.sources.order_by("position"))
        if len(retained) != len(sources):
            return False
        if any(
            row.position != source.source_position or row.file_id != getattr(source.file, "pk", None)
            or row.message_part_id != getattr(source.message_part, "pk", None)
            or row.content_hash != source.content_hash
            for row, source in zip(retained, sources)
        ):
            return False
        retained_pages = list(existing.pages.order_by("position"))
        if len(retained_pages) != len(pages) or len(pages) != len(page_results):
            return False
        if any(
            row.source.position != page.source_position or row.source_page != page.page_position
            or row.position != position or row.width != page.width or row.height != page.height
            or row.dpi != page.dpi or row.duration_ms != max(result.duration_ms, 0)
            or not json_values_equal(row.result, result.value)
            or not json_values_equal(row.engine_metadata, result.engine_metadata or {})
            for position, (row, page, result) in enumerate(zip(retained_pages, pages, page_results))
        ):
            return False
        retained_parts = list(existing.parts.order_by("position"))
        return len(retained_parts) == len(parts) and all(
            row.position == position and row.source.position == part.source_position
            and row.source_page == part.source_page and row.mime_type == part.mime_type
            and row.kind == part.kind and row.method == part.method
            and row.content_hash == part.content_hash and json_values_equal(row.value, part.value)
            and row.width == part.width and row.height == part.height and row.dpi == part.dpi
            and row.duration_ms == max(part.duration_ms, 0)
            and json_values_equal(row.metadata, part.metadata or {})
            and json_values_equal(row.claims, _claims_for_part(existing.claims, position))
            for position, (row, part) in enumerate(zip(retained_parts, parts))
        )

    def _evidence_models(self) -> tuple[type[Any], type[Any], type[Any]]:
        registry = self.model._meta.apps
        return (
            registry.get_model("workflows_extraction", "ExtractionSource"),
            registry.get_model("workflows_extraction", "ExtractionPage"),
            registry.get_model("workflows_extraction", "ExtractionPart"),
        )

    def _persist_fresh_evidence(
        self,
        extraction: Any,
        sources: Sequence[DocumentSource],
        pages: Sequence[PageImage],
        page_results: Sequence[PageResult],
        parts: Sequence[DocumentPart],
    ) -> None:
        source_model, page_model, part_model = self._evidence_models()
        retained_sources = source_model._base_manager.using(self.db).bulk_create(
            [
                source_model(
                    extraction=extraction,
                    position=source.source_position,
                    file=source.file,
                    message_part=source.message_part,
                    content_hash=source.content_hash,
                )
                for source in sources
            ]
        )
        source_by_position = {source.position: source for source in retained_sources}
        page_model._base_manager.using(self.db).bulk_create(
            [
                page_model(
                    extraction=extraction,
                    source=source_by_position[page.source_position],
                    position=position,
                    source_page=page.page_position,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    duration_ms=max(result.duration_ms, 0),
                    result=result.value,
                    engine_metadata=result.engine_metadata or {},
                )
                for position, (page, result) in enumerate(zip(pages, page_results))
            ]
        )
        claims = extraction.provenance["claims"]
        part_model._base_manager.using(self.db).bulk_create(
            [
                part_model(
                    extraction=extraction,
                    source=source_by_position[part.source_position],
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
                    claims=_claims_for_part(claims, position),
                    metadata=part.metadata or {},
                    duration_ms=max(part.duration_ms, 0),
                )
                for position, part in enumerate(parts)
            ]
        )

    def _clone_retained_evidence(self, extraction: Any, original: Any) -> None:
        source_model, page_model, part_model = self._evidence_models()
        original_sources = list(
            source_model._base_manager.using(self.db).filter(extraction=original).order_by("position")
        )
        if [source.position for source in original_sources] != list(range(len(original_sources))):
            raise ValidationError({"extraction": "The retained source ordering is invalid."})
        retained_sources = source_model._base_manager.using(self.db).bulk_create(
            [
                source_model(
                    extraction=extraction,
                    position=source.position,
                    file_id=source.file_id,
                    message_part_id=source.message_part_id,
                    content_hash=source.content_hash,
                )
                for source in original_sources
            ]
        )
        source_by_id = {
            original_source.pk: retained_source
            for original_source, retained_source in zip(original_sources, retained_sources)
        }
        original_pages = list(
            page_model._base_manager.using(self.db).filter(extraction=original).order_by("position")
        )
        if [page.position for page in original_pages] != list(range(len(original_pages))):
            raise ValidationError({"extraction": "The retained page ordering is invalid."})
        page_model._base_manager.using(self.db).bulk_create(
            [
                page_model(
                    extraction=extraction,
                    source=source_by_id[page.source_id],
                    position=page.position,
                    source_page=page.source_page,
                    width=page.width,
                    height=page.height,
                    dpi=page.dpi,
                    duration_ms=page.duration_ms,
                    result=page.result,
                    engine_metadata=page.engine_metadata,
                )
                for page in original_pages
            ]
        )
        original_parts = list(
            part_model._base_manager.using(self.db).filter(extraction=original).order_by("position")
        )
        if [part.position for part in original_parts] != list(range(len(original_parts))):
            raise ValidationError({"extraction": "The retained part ordering is invalid."})
        claims = extraction.provenance["claims"]
        part_model._base_manager.using(self.db).bulk_create(
            [
                part_model(
                    extraction=extraction,
                    source=source_by_id[part.source_id],
                    position=part.position,
                    source_page=part.source_page,
                    mime_type=part.mime_type,
                    kind=part.kind,
                    method=part.method,
                    content_hash=part.content_hash,
                    width=part.width,
                    height=part.height,
                    dpi=part.dpi,
                    value=part.value,
                    claims=_claims_for_part(claims, part.position),
                    metadata=part.metadata,
                    duration_ms=part.duration_ms,
                )
                for part in original_parts
            ]
        )


def _claims_for_part(claims: dict[str, list[dict[str, Any]]], position: int) -> dict[str, list[dict[str, Any]]]:
    return {
        pointer: matching
        for pointer, entries in claims.items()
        if (matching := [claim for claim in entries if claim["part_position"] == position])
    }


def _stable_correction_provenance(provenance: Any) -> dict[str, Any]:
    """Exclude the first materializer actor from exact authority reuse checks."""

    value = deepcopy(provenance)
    corrections = value.get("corrections")
    if isinstance(corrections, list) and corrections and isinstance(corrections[-1], dict):
        corrections[-1].pop("recorded_by", None)
    return value


def _field_equal(existing: Any, field: str, requested: Any) -> bool:
    if field in {"model", "recognition_model", "content_type"}:
        return getattr(existing, f"{field}_id") == (requested.pk if requested is not None else None)
    if field in {"content_type_id", "created_by_id"}:
        return getattr(existing, field) == requested
    return json_values_equal(getattr(existing, field), requested)


def _result_selectors(result: Any, layout: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Expand domain-declared JSON locators without reading domain field names."""

    if not isinstance(result, dict) or not result:
        return ()
    if not isinstance(layout, dict):
        raise ValidationError({"extraction": "The evidence layout is invalid."})
    document_collection = layout.get("document_collection", "")
    line_collection = layout.get("line_collection", "")
    root_document_on_missing = layout.get("root_document_on_missing", False)
    if type(root_document_on_missing) is not bool:
        raise ValidationError({"extraction": "The root document fallback policy must be a boolean."})
    if not all(isinstance(pointer, str) and (not pointer or pointer.startswith("/"))
               for pointer in (document_collection, line_collection)):
        raise ValidationError({"extraction": "The evidence layout requires JSON pointers."})
    from angee.workflows_extraction.service import json_pointer_value

    try:
        documents = json_pointer_value(result, document_collection) if document_collection else None
    except KeyError:
        if not root_document_on_missing:
            raise ValidationError({"extraction": "The declared document collection is absent."})
        documents = None
    if documents is not None and not isinstance(documents, list):
        raise ValidationError({"extraction": "The declared document collection must be a list."})
    if isinstance(documents, list):
        items = tuple((f"{document_collection}/{index}", document) for index, document in enumerate(documents))
    else:
        items = (("", result),)
    selectors = []
    for selector, document in items:
        if not isinstance(document, dict):
            raise ValidationError({"extraction": "A logical document must be an object."})
        try:
            lines = json_pointer_value(document, line_collection) if line_collection else None
        except KeyError:
            lines = None
        if lines is not None and not isinstance(lines, list):
            raise ValidationError({"extraction": "The declared source-line collection must be a list."})
        selectors.append((selector, tuple(f"{selector}{line_collection}/{i}" for i in range(len(lines or ())))))
    return tuple(selectors)


def _document_mapping(
    result: Any, *, layout: Any, original: Any | None, identity_mapping: Any,
    retired_identities: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Allocate once, or carry reviewed correspondence without matching printed facts."""

    requested = _result_selectors(result, layout)
    previous = tuple(original.document_refs) if original is not None else ()
    mapping = dict(identity_mapping or {})
    retirement = dict(retired_identities or {})
    if original is not None and previous and not mapping and json_values_equal(original.result, result):
        mapping = {
            selector: identity
            for ref in previous
            for selector, identity in (
                (ref.selector, ref.identity),
                *((line.selector, line.identity) for line in ref.lines),
            )
        }
    old_docs = {ref.identity: ref for ref in previous}
    old_lines = {line.identity: line for ref in previous for line in ref.lines}
    if original is not None and len(previous) > 1 and mapping == {} and requested:
        raise ValidationError({"extraction": "Multiple logical documents require explicit identity correspondence."})
    rows: list[dict[str, Any]] = []
    carried_docs: set[str] = set()
    carried_lines: set[str] = set()
    for selector, line_selectors in requested:
        identity = mapping.get(selector)
        if not previous and identity not in (None, "new"):
            raise ValidationError({"extraction": "Initial document identities are allocated by retention."})
        if (identity is None and len(previous) == len(requested) == 1
                and previous[0].selector == selector):
            identity = previous[0].identity
        if identity == "new" or (identity is None and not previous):
            identity = uuid4().hex
        if identity is None:
            raise ValidationError({"extraction": "A new logical document must be explicitly mapped."})
        if identity in carried_docs or identity in old_lines or (previous and identity not in old_docs and selector not in mapping):
            raise ValidationError({"extraction": "The document identity correspondence is invalid."})
        if previous and identity not in old_docs and mapping.get(selector) != "new":
            raise ValidationError({"extraction": "A newly introduced document must be declared new."})
        carried_docs.add(identity)
        prior_lines = old_docs[identity].lines if identity in old_docs else ()
        if len(prior_lines) > 1 and not all(selector in mapping for selector in line_selectors):
            raise ValidationError({"extraction": "Multiple source lines require explicit identity correspondence."})
        line_rows = []
        for line_selector in line_selectors:
            line_id = mapping.get(line_selector)
            if not previous and line_id not in (None, "new"):
                raise ValidationError({"extraction": "Initial source-line identities are allocated by retention."})
            if (line_id is None and len(prior_lines) == len(line_selectors) == 1
                    and prior_lines[0].selector == line_selector):
                line_id = prior_lines[0].identity
            if line_id == "new" or (line_id is None and not previous):
                line_id = uuid4().hex
            if line_id is None:
                raise ValidationError({"extraction": "A new source line must be explicitly mapped."})
            if line_id in carried_lines or line_id in old_docs:
                raise ValidationError({"extraction": "The source-line correspondence is invalid."})
            if previous and line_id not in old_lines and mapping.get(line_selector) != "new":
                raise ValidationError({"extraction": "A newly introduced source line must be declared new."})
            carried_lines.add(line_id)
            line_rows.append({"identity": line_id, "selector": line_selector})
        rows.append({"identity": identity, "selector": selector, "lines": line_rows})
    if set(mapping) - {selector for selector, lines in requested for selector in (selector, *lines)}:
        raise ValidationError({"extraction": "Identity mapping references absent selectors."})
    absent_docs = set(old_docs) - carried_docs
    absent_lines = set(old_lines) - carried_lines
    if set(retirement) != absent_docs | absent_lines or any(
        not isinstance(reason, str) or not reason.strip() for reason in retirement.values()
    ):
        raise ValidationError({"extraction": "Retired identities need explicit retained reasons."})
    retired = [
        {"identity": identity, "kind": "document" if identity in absent_docs else "line", "reason": reason}
        for identity, reason in sorted(retirement.items())
    ]
    return rows, retired
