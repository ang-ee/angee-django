"""Retained scalar authority survives inference and reviewed identity movement."""

from copy import deepcopy

import pytest
from django.core.exceptions import ValidationError

from angee.workflows_extraction.contracts import DocumentResult
from angee.workflows_extraction.inference import RETAINED_AUTHORITY_COMPLETION_REVIEW
from tests.extraction_models import Extraction


def authority(*, result, claims, lines=(), corrections=()):
    """Construct immutable value state without persistence or provider calls."""
    return Extraction(
        result=result,
        document_map=[
            {
                "identity": "document",
                "selector": "/documents/0",
                "lines": [
                    {"identity": identity, "selector": f"/documents/0/lines/{index}"}
                    for index, identity in enumerate(lines)
                ],
            }
        ],
        provenance={"claims": claims, "corrections": list(corrections)},
    )


def test_inference_preserves_claimed_and_corrected_values_without_mutating_inputs():
    original = {"documents": [{"title": "Source", "note": "Corrected", "lines": [{"text": "A"}, {"text": "B"}]}]}
    retained = authority(
        result=original,
        lines=("first", "second"),
        claims={
            "/documents/0/title": [{"part_position": 0, "start": 0, "end": 6}],
            "/documents/0/lines/0/text": [{"part_position": 0, "start": 7, "end": 8}],
        },
        corrections=[
            {
                "original_extraction_id": "earlier",
                "original_extraction_revision": 1,
                "decision_id": "review",
                "corrected_paths": ["/documents/0/note", "/documents/0/lines/0/text"],
            }
        ],
    )
    candidate = DocumentResult(
        {"documents": [{"title": "Changed", "extra": "Inferred", "lines": [{"text": "B"}, {"text": "Changed A"}]}]},
        (),
        {"/documents/0/title": [{"part_position": 1}], "/documents/0/note": [{"part_position": 1}]},
    )
    before = deepcopy(candidate)
    mapping = {"/documents/0": "document", "/documents/0/lines/0": "second", "/documents/0/lines/1": "first"}
    result = retained.preserve_authority(candidate, identity_mapping=mapping)
    document = result.value["documents"][0]
    assert document == {
        "title": "Source",
        "note": "Corrected",
        "extra": "Inferred",
        "lines": [{"text": "B"}, {"text": "A"}],
    }
    assert result.claims["/documents/0/title"] == retained.claims["/documents/0/title"]
    assert result.claims["/documents/0/lines/1/text"] == retained.claims["/documents/0/lines/0/text"]
    assert "/documents/0/note" not in result.claims
    assert result.provider_metadata["unresolved_reasons"] == [RETAINED_AUTHORITY_COMPLETION_REVIEW]
    assert candidate == before and retained.result == original
    corrections = retained.retained_corrections(result.value, identity_mapping=mapping)
    assert corrections[0]["corrected_paths"] == ["/documents/0/note", "/documents/0/lines/1/text"]
    changed = deepcopy(result.value)
    changed["documents"][0]["note"] = "Changed"
    assert retained.retained_corrections(changed, identity_mapping=mapping)[0]["corrected_paths"] == [
        "/documents/0/lines/1/text",
    ]


def test_missing_nested_authority_materializes_escaped_path_and_remaps_carrier():
    retained = authority(
        result={"documents": [{"details": {"a/b": None}}]},
        claims={"/documents/0/details/a~1b": [{"part_position": 3, "start": 0, "end": 1}]},
    )
    result = retained.preserve_authority(
        DocumentResult({"documents": [{}]}, (), {}),
        identity_mapping={"/documents/0": "document"},
        claim_part_positions={3: 0},
    )
    assert result.value == retained.result
    assert result.claims["/documents/0/details/a~1b"][0]["part_position"] == 0
    assert retained.claims["/documents/0/details/a~1b"][0]["part_position"] == 3


def test_retired_identity_authority_is_not_copied_to_the_remaining_line():
    retained = authority(
        result={"documents": [{"lines": [{"text": "A"}, {"text": "B"}]}]},
        lines=("first", "second"),
        claims={"/documents/0/lines/0/text": [{"part_position": 0}]},
    )
    candidate = DocumentResult({"documents": [{"lines": [{"text": "B"}]}]}, (), {})
    result = retained.preserve_authority(
        candidate,
        identity_mapping={"/documents/0": "document", "/documents/0/lines/0": "second"},
        retired_identities={"first": "Removed"},
    )
    assert result.value == candidate.value and result.claims == {}


def test_authority_requires_correspondence_and_cannot_cover_identity_containers():
    retained = authority(result={"documents": [{"title": "A"}]}, claims={"/documents/0/title": [{"part_position": 0}]})
    with pytest.raises(ValidationError, match="explicit identity correspondence"):
        retained.preserve_authority(DocumentResult({"documents": [{"title": "B"}]}, (), {}), identity_mapping={})
    retained.provenance = {"claims": {"/documents": [{"part_position": 0}]}}
    with pytest.raises(ValidationError, match="identity container"):
        retained.preserve_authority(DocumentResult({"documents": []}, (), {}), identity_mapping={})
