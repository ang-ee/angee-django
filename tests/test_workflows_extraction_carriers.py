"""Profile-owned carriers compose with neutral native acquisition."""

import hashlib
from types import SimpleNamespace

import pytest

from angee.workflows_extraction.contracts import DocumentPipelineError, DocumentSource, ExtractionPartKind
from angee.workflows_extraction.profiles import UnconfiguredExtractionProfile
from angee.workflows_extraction.routing import acquire_native_parts
from tests.extraction_profiles import RecordCarrierProfile


@pytest.mark.parametrize("file", [SimpleNamespace(filename="retained.eml"), SimpleNamespace(name="retained.eml")])
def test_source_filename_routes_untyped_message_attachment(file: SimpleNamespace) -> None:
    source = DocumentSource(
        0, "a" * 64, "application/octet-stream", b"Subject: Retained\n\nMessage evidence", file=file,
    )

    acquired = acquire_native_parts((source,), profile=UnconfiguredExtractionProfile())

    assert acquired.parts[0].method == "message_attachment"
    assert acquired.parts[0].value == "Retained\nMessage evidence"


def test_profile_carrier_precedes_native_text_and_retains_original_digest() -> None:
    source = DocumentSource(3, "snapshot", "text/plain", b"record:R-7")
    acquired = acquire_native_parts((source,), profile=RecordCarrierProfile())

    (part,) = acquired.parts
    assert part.kind == ExtractionPartKind.STRUCTURED
    assert part.value == {"number": "R-7"}
    assert part.source_position == 3
    assert part.method == "structured:record"
    assert part.content_hash == hashlib.sha256(source.content).hexdigest()
    assert acquired.pages[0].native_parts == (part,)
    assert acquired.recognition_pages == ()


def test_unrecognized_carriers_fall_back_to_generic_native_acquisition() -> None:
    source = DocumentSource(0, "snapshot", "text/plain", b"ordinary record text")
    acquired = acquire_native_parts((source,), profile=RecordCarrierProfile())
    assert acquired.parts[0].kind == ExtractionPartKind.NATIVE_TEXT
    assert acquired.parts[0].value == "ordinary record text"

    envelope = DocumentSource(1, "snapshot", "text/plain", b"record:R-8")
    unconfigured = acquire_native_parts((envelope,), profile=UnconfiguredExtractionProfile())
    assert unconfigured.parts[0].kind == ExtractionPartKind.NATIVE_TEXT
    assert unconfigured.parts[0].value == "record:R-8"


def test_carrier_failure_retains_prior_evidence_and_acquisition_stage() -> None:
    sources = (
        DocumentSource(0, "first", "text/plain", b"record:R-9"),
        DocumentSource(1, "second", "text/plain", b"record:\xff"),
    )
    with pytest.raises(DocumentPipelineError) as failure:
        acquire_native_parts(sources, profile=RecordCarrierProfile())

    assert failure.value.stage == "acquisition"
    assert failure.value.parts[0].value == {"number": "R-9"}
