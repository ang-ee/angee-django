"""Append-only evidence collections and atomic revision allocation."""

from collections.abc import Sequence
from typing import Any

from django.db import IntegrityError, transaction
from rebac import system_context

from angee.base.models import AngeeManager, AngeeQuerySet
from angee.workflows_ocr.engines import DocumentPart, DocumentSource, PageImage, PageResult


class ImmutableEvidenceQuerySet(AngeeQuerySet[Any]):
    """Prevent post-insert mutation and deletion through bulk ORM paths."""

    def update(self, **kwargs: Any) -> int:
        raise ValueError("Extraction evidence is immutable.")

    def delete(self) -> tuple[int, dict[str, int]]:
        raise ValueError("Extraction evidence is retained and cannot be deleted through the ORM.")


ImmutableEvidenceManager = AngeeManager.from_queryset(ImmutableEvidenceQuerySet)


class ExtractionManager(ImmutableEvidenceManager):
    """Persist one authorized engine result and all of its ordered evidence atomically."""

    def create_revision(
        self,
        *,
        sources: Sequence[DocumentSource],
        pages: Sequence[PageImage],
        page_results: Sequence[PageResult],
        parts: Sequence[DocumentPart],
        **values: Any,
    ) -> Any:
        """Reuse exact requests or allocate the next revision, including concurrent first writes.

        The service authorizes and finishes external I/O before entering this
        factory. Uniqueness remains authoritative on SQLite, where row locks
        are unavailable, and for a lineage with no existing row to lock.
        """

        registry = self.model._meta.apps
        source_model = registry.get_model("workflows_ocr", "ExtractionSource")
        page_model = registry.get_model("workflows_ocr", "ExtractionPage")
        part_model = registry.get_model("workflows_ocr", "ExtractionPart")
        with system_context(reason="workflows_ocr.extraction.create_revision"):
            for attempt in range(3):
                try:
                    with transaction.atomic(using=self.db):
                        existing = self.filter(reuse_key=values["reuse_key"]).first()
                        if existing is not None:
                            return existing
                        previous = (
                            self.lock_if_supported()
                            .filter(lineage_key=values["lineage_key"])
                            .order_by("-revision")
                            .first()
                        )
                        extraction = self.create(revision=previous.revision + 1 if previous else 1, **values)
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
                                    claims={
                                        pointer: matching
                                        for pointer, entries in claims.items()
                                        if (
                                            matching := [
                                                claim for claim in entries if claim["part_position"] == position
                                            ]
                                        )
                                    },
                                    metadata=part.metadata or {},
                                    duration_ms=max(part.duration_ms, 0),
                                )
                                for position, part in enumerate(parts)
                            ]
                        )
                        return extraction
                except IntegrityError:
                    duplicate = self.filter(reuse_key=values["reuse_key"]).first()
                    if duplicate is not None:
                        return duplicate
                    if attempt == 2:
                        raise
