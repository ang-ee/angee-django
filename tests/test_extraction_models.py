"""Durable evidence, identity continuity, and retention authorization proofs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from threading import Barrier

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from rebac import actor_context, system_context

from angee.workflows_extraction.contracts import (
    DocumentPart,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    PageImage,
    PageResult,
)
from angee.workflows_extraction.managers import StaleExtraction
from angee.workflows_extraction.profiles import ExtractionProfile
from tests.conftest import Drive, File, create_platform_admin
from tests.extraction_models import Extraction, ExtractionLineage, ExtractionPage, ExtractionPart, ExtractionSource
from tests.test_storage import drive as drive


class NotesProfile(ExtractionProfile):
    """Declare document and line locations independently of their values."""

    key = "retention_notes"
    evidence_layout = {"document_collection": "/documents", "line_collection": "/lines"}


SCHEMA = {
    "$id": "urn:test:extraction:notes",
    "type": "object",
    "required": ["documents"],
    "properties": {
        "documents": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["title", "lines"],
                "properties": {
                    "title": {"type": "string"},
                    "optional": {"type": ["string", "null"]},
                    "lines": {
                        "type": "array",
                        "items": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}},
                    },
                },
            },
        },
    },
}


@pytest.fixture
def evidence(drive, settings):
    """Retain real storage-backed evidence with a registry-selected profile."""

    settings.ANGEE_EXTRACTION_PROFILE_CLASSES = {
        **settings.ANGEE_EXTRACTION_PROFILE_CLASSES,
        NotesProfile.key: "tests.test_extraction_models.NotesProfile",
    }
    content = b"Note\nFirst line\nSecond line"
    with actor_context(drive.alice):
        source = File.objects.ingest_bytes(content, filename="note.txt", drive_id=str(drive.sqid))
    document_source = DocumentSource(0, source.content_hash, "text/plain", content, file=source)
    part = DocumentPart(
        0, 0, "text/plain", ExtractionPartKind.NATIVE_TEXT, content.decode(), "native", source.content_hash,
    )
    result = DocumentResult(
        value={"documents": [{
            "title": "Note", "optional": None, "lines": [{"text": "First line"}, {"text": "Second line"}],
        }]},
        parts=(part,),
        claims={"/documents/0/title": [{"part_position": 0, "start": 0, "end": 4}]},
    )
    defaults = {
        "sources": (document_source,), "result": result, "target": source, "actor": drive.alice,
        "profile": NotesProfile.key, "profile_config": {}, "schema": SCHEMA, "request_key": "first",
    }

    def retain(**changes):
        values = {**defaults, **changes}
        with actor_context(values["actor"]):
            return Extraction.objects.retain_result(**values)

    with actor_context(drive.alice):
        yield retain, defaults


def test_retention_preserves_sources_parts_pages_and_typed_missing_values(evidence):
    retain, values = evidence
    image = PageImage(0, 0, "image/png", b"retained raster", 40, 60, 100)
    page = PageResult({"text": "Note"}, duration_ms=12)
    row = retain(pages=(image,), page_results=(page,))

    assert row.revision == 1
    assert row.sources.count() == row.parts.count() == row.pages.count() == 1
    retained_source = row.document_sources()[0]
    assert retained_source.file.pk == values["target"].pk
    assert retained_source.content_hash == values["sources"][0].content_hash
    assert retained_source.mime_type == "text/plain"
    assert row.document_parts()[0].value == values["result"].parts[0].value
    reference = row.document_refs[0]
    document, selected = row.selected_document(reference.identity)
    assert selected == reference and document["title"] == "Note"
    line, line_reference = row.selected_line(reference.identity, reference.lines[1].identity)
    assert line["text"] == "Second line" and line_reference == reference.lines[1]
    assert row.fact("/documents/0/optional") is None
    assert row.fact_authority("/documents/0/title").kind == "source"
    with pytest.raises(KeyError):
        row.fact("/documents/0/absent")
    with pytest.raises(KeyError):
        row.selected_line(reference.identity, "unknown")
    assert ExtractionLineage.objects.get(pk=row.lineage_key).head_id == row.pk


def test_exact_retry_reuses_revision_and_conflicting_request_is_rejected(evidence):
    retain, values = evidence
    first = retain()
    assert retain().pk == first.pk
    changed = deepcopy(values["result"].value)
    changed["documents"][0]["optional"] = "Other"
    with pytest.raises(ValidationError):
        retain(result=replace(values["result"], value=changed))
    assert Extraction.objects.count() == 1


def test_lineage_compare_and_swap_rejects_a_stale_parent(evidence):
    retain, _values = evidence
    first = retain()
    second = retain(base=first, request_key="second")
    assert second.revision == 2
    assert second.document_refs == first.document_refs
    with pytest.raises(ValidationError):
        retain(base=first, request_key="stale")
    assert ExtractionLineage.objects.get(pk=first.lineage_key).head_id == second.pk
    assert Extraction.objects.count() == 2


def test_source_order_does_not_fork_the_target_lineage(evidence):
    retain, values = evidence
    other = File.objects.ingest_bytes(b"Another note", filename="other.txt", drive_id=str(values["target"].drive.sqid))
    original = values["sources"][0]
    additional = DocumentSource(1, other.content_hash, "text/plain", b"Another note", file=other)
    first = retain(sources=(original, additional))
    reordered = (replace(additional, source_position=0), replace(original, source_position=1))
    result = replace(values["result"], parts=(replace(values["result"].parts[0], source_position=1),))
    second = retain(sources=reordered, result=result, request_key="reordered")
    assert second.lineage_key == first.lineage_key
    assert second.revision == first.revision + 1
    assert list(second.sources.values_list("file_id", flat=True)) == [other.pk, original.file.pk]


@pytest.mark.parametrize("invalid", [
    "schema", "source_position", "claim_position", "claim_shape", "claim_span", "source_digest",
    "unknown_role", "unconfigured_role", "nonfinite_metadata", "page_source",
])
def test_invalid_candidate_rolls_back_the_whole_evidence_family(evidence, invalid):
    retain, values = evidence
    result = values["result"]
    sources = values["sources"]
    pages = page_results = ()
    if invalid == "schema":
        result = replace(result, value={"documents": [{"title": 3, "lines": []}]})
    elif invalid == "source_position":
        result = replace(result, parts=(replace(result.parts[0], source_position=9),))
    elif invalid == "claim_position":
        result = replace(result, claims={"/documents/0/title": [{"part_position": 9, "start": 0, "end": 4}]})
    elif invalid == "claim_shape":
        result = replace(result, claims={"/documents/0/title": ["not a claim"]})
    elif invalid == "claim_span":
        result = replace(result, claims={"/documents/0/title": [{"part_position": 0, "start": 0, "end": 999}]})
    elif invalid == "source_digest":
        sources = (replace(sources[0], content_hash="a" * 64),)
    elif invalid == "unknown_role":
        result = replace(result, used_model_roles=("unknown",))
    elif invalid == "unconfigured_role":
        result = replace(result, used_model_roles=("mapping",))
    elif invalid == "nonfinite_metadata":
        result = replace(result, provider_metadata={"duration": float("nan")})
    else:
        pages = (PageImage(99, 0, "image/png", b"raster", 40, 60, 100),)
        page_results = (PageResult({"text": "Note"}),)
    with pytest.raises(ValidationError):
        retain(result=result, sources=sources, pages=pages, page_results=page_results)
    for model in (Extraction, ExtractionLineage, ExtractionSource, ExtractionPage, ExtractionPart):
        assert model.objects.count() == 0


def test_competing_revision_requests_allocate_exactly_one_successor(evidence):
    """Race native PostgreSQL locks; SQLite checks the same CAS serially."""

    retain, _values = evidence
    base = retain()
    parallel = connection.vendor == "postgresql"
    ready = Barrier(2) if parallel else None

    def revise(key):
        close_old_connections()
        try:
            if ready is not None:
                ready.wait(timeout=10)
            try:
                return retain(base=base, request_key=key).pk
            except StaleExtraction:
                return None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2 if parallel else 1) as pool:
        results = list(pool.map(revise, ("left", "right")))
    assert results.count(None) == 1
    winner = next(value for value in results if value is not None)
    assert ExtractionLineage.objects.get(pk=base.lineage_key).head_id == winner
    assert list(Extraction.objects.order_by("revision").values_list("revision", flat=True)) == [1, 2]


def test_explicit_correspondence_preserves_reordered_lines_and_records_retirement(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    changed = deepcopy(values["result"].value)
    changed["documents"][0]["lines"] = [{"text": "Second line"}, {"text": "New line"}]
    next_row = retain(
        base=first, request_key="correspondence", result=replace(values["result"], value=changed),
        identity_mapping={
            "/documents/0": reference.identity,
            "/documents/0/lines/0": reference.lines[1].identity,
            "/documents/0/lines/1": "new",
        },
        retired_identities={reference.lines[0].identity: "Removed from the revised evidence"},
    )
    next_ref = next_row.document_refs[0]
    assert next_ref.identity == reference.identity
    assert next_ref.lines[0].identity == reference.lines[1].identity
    assert next_ref.lines[1].identity not in {item.identity for item in reference.lines}
    assert next_row.retired_identities == [{
        "identity": reference.lines[0].identity, "kind": "line", "reason": "Removed from the revised evidence",
    }]


def test_unknown_correspondence_cannot_create_arbitrary_identities(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    with pytest.raises(ValidationError):
        retain(
            base=first, request_key="invalid-correspondence", result=values["result"],
            identity_mapping={
                "/documents/0": reference.identity,
                "/documents/0/lines/0": "invented",
                "/documents/0/lines/1": reference.lines[1].identity,
            },
        )
    assert Extraction.objects.count() == 1


def test_retained_rows_reject_instance_and_collection_mutation(evidence):
    retain, _values = evidence
    extraction = retain(
        pages=(PageImage(0, 0, "image/png", b"raster", 40, 60, 100),),
        page_results=(PageResult({"text": "Note"}),),
    )
    with system_context(reason="tests.extraction immutable evidence"):
        rows = (
            extraction, ExtractionLineage.objects.get(pk=extraction.lineage_key),
            extraction.sources.get(), extraction.pages.get(), extraction.parts.get(),
        )
        for row in rows:
            with pytest.raises((ValidationError, ValueError)):
                row.save()
            with pytest.raises((ValidationError, ValueError)):
                row.delete()
            for manager in (type(row).objects, type(row)._base_manager):
                with pytest.raises(ValidationError):
                    manager.filter(pk=row.pk).update(pk=row.pk)
                with pytest.raises(ValidationError):
                    manager.filter(pk=row.pk).delete()
    assert Extraction.objects.count() == 1


@pytest.mark.parametrize("protected", ["source", "target"])
def test_retention_requires_independent_source_and_target_read_access(evidence, protected):
    retain, values = evidence
    outsider = get_user_model().objects.create_user(username="evidence-outsider")
    with actor_context(outsider), system_context(reason="tests.extraction separate source and target"):
        other_drive = Drive.objects.create(
            backend=values["target"].drive.backend, slug="private", name="Private", prefix="private",
            created_by=outsider, updated_by=outsider,
        )
        other = File.objects.ingest_bytes(
            b"Other evidence", filename="other.txt", drive_id=str(other_drive.sqid), owner_id=outsider.pk,
        )
    if protected == "source":
        changes = {"sources": (replace(values["sources"][0], file=other),)}
    else:
        changes = {"target": other}
    with pytest.raises(PermissionDenied):
        retain(**changes)
    assert Extraction.objects.count() == 0


def test_target_reader_inherits_evidence_access_without_receiving_ownership(evidence):
    retain, values = evidence
    owner = values["actor"]
    author = create_platform_admin("evidence-author")
    row = retain(actor=author)
    outsider = get_user_model().objects.create_user(username="evidence-reader-outsider")
    assert row.created_by_id == author.pk
    for evidence_row in (row, row.sources.get(), row.parts.get()):
        assert evidence_row.with_actor(owner).has_access("read")
        assert not evidence_row.with_actor(outsider).has_access("read")
