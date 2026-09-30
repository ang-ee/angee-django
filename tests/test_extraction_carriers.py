"""Authoritative claims follow unique physical carriers across source reordering."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.extraction.contracts import DocumentPart, DocumentSource, ExtractionPartKind
from tests.conftest import Drive, File
from tests.test_extraction_models import evidence as evidence
from tests.test_storage import drive as drive


def reviewed_mapping(extraction):
    """Describe the explicit correspondence for reversing the fixture's two lines."""
    document = extraction.document_refs[0]
    return {
        document.selector: document.identity,
        document.lines[0].selector: document.lines[1].identity,
        document.lines[1].selector: document.lines[0].identity,
    }


def reversed_candidate(values):
    candidate = deepcopy(values["result"].value)
    candidate["documents"][0]["lines"].reverse()
    candidate["documents"][0]["title"] = "Changed by inference"
    return candidate


def test_reordered_sources_keep_physical_authority_and_remap_claim_positions(evidence):
    retain, values = evidence
    original = values["sources"][0]
    extra = File.objects.ingest_bytes(
        b"Additional evidence", filename="extra.txt", drive_id=str(original.file.drive.sqid)
    )
    extra_source = DocumentSource(1, extra.content_hash, "text/plain", b"Additional evidence", file=extra)
    extra_part = DocumentPart(
        1, 0, "text/plain", ExtractionPartKind.NATIVE_TEXT, "Additional evidence", "native", extra.content_hash
    )
    first = retain(
        sources=(original, extra_source), result=replace(values["result"], parts=(*values["result"].parts, extra_part))
    )
    reordered_sources = (replace(extra_source, source_position=0), replace(original, source_position=1))
    reordered_parts = (
        replace(extra_part, source_position=0),
        replace(values["result"].parts[0], source_position=1, duration_ms=12, metadata={"attempt": 2}),
    )
    candidate = replace(values["result"], value=reversed_candidate(values), parts=reordered_parts, claims={})
    held = retain(request_key="reordered-hold", sources=reordered_sources, result=candidate)
    assert held.awaiting_correspondence and held.lineage_key == first.lineage_key
    assert first.authority_carrier_positions(held) == {0: 1, 1: 0}
    resolved = retain(
        base=held,
        request_key="reordered-resolved",
        sources=(),
        result=candidate,
        identity_mapping=reviewed_mapping(first),
    )
    assert resolved.result["documents"][0]["title"] == "Note"
    assert resolved.claims["/documents/0/title"] == [{"part_position": 1, "start": 0, "end": 4}]


def test_identical_text_from_another_source_cannot_inherit_authority(evidence):
    retain, values = evidence
    first = retain()
    original = values["sources"][0]
    with system_context(reason="tests.extraction distinct carrier sources"):
        other_drive = Drive.objects.create(
            backend=original.file.drive.backend,
            slug="replacement",
            name="Replacement",
            prefix="replacement",
            created_by=values["actor"],
            updated_by=values["actor"],
        )
    replacement = File.objects.ingest_bytes(
        original.content,
        filename="replacement.txt",
        drive_id=str(other_drive.sqid),
        owner_id=values["actor"].pk,
    )
    assert replacement.pk != original.file.pk and replacement.content_hash == original.content_hash
    replacement_source = DocumentSource(1, replacement.content_hash, "text/plain", original.content, file=replacement)
    candidate = replace(
        values["result"],
        value=reversed_candidate(values),
        claims={},
        parts=(replace(values["result"].parts[0], source_position=1),),
    )
    held = retain(request_key="replacement-hold", sources=(original, replacement_source), result=candidate)
    assert held.awaiting_correspondence and held.lineage_key == first.lineage_key
    assert first.authority_carrier_positions(held) == {}
    with pytest.raises(ValidationError, match="lacks a retained carrier"):
        retain(
            base=held,
            request_key="replacement-resolved",
            sources=(),
            result=candidate,
            identity_mapping=reviewed_mapping(first),
        )


def test_duplicate_physical_carriers_are_ambiguous(evidence):
    retain, values = evidence
    first = retain()
    part = values["result"].parts[0]
    candidate = replace(values["result"], value=reversed_candidate(values), parts=(part, part), claims={})
    held = retain(request_key="duplicate-hold", result=candidate)
    assert held.awaiting_correspondence
    assert first.authority_carrier_positions(held) == {}
    with pytest.raises(ValidationError, match="lacks a retained carrier"):
        retain(base=held, request_key="duplicate-resolved", result=candidate, identity_mapping=reviewed_mapping(first))
