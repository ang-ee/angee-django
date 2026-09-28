"""Pure extraction profile, grounding and strict pointer contracts."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError

from angee.base.impl import resolve_impl_class
from angee.workflows_extraction.contracts import (
    DocumentPart,
    DocumentPipelineError,
    DocumentRef,
    DocumentResult,
    DocumentSource,
    ExtractionPartKind,
    LineRef,
    PipelineError,
    Result,
    Source,
)
from angee.workflows_extraction.inference import derive_text_claims
from angee.workflows_extraction.pointers import (
    JSON_POINTER_MISSING,
    implicit_identity_correspondence,
    json_pointer_value,
    json_pointer_value_or_missing,
    materialize_missing_json_pointer_path,
    result_selectors,
    set_json_pointer,
)
from angee.workflows_extraction.profiles import (
    ExtractionProfile,
    UnconfiguredExtractionProfile,
    authored_profile_config,
)


@pytest.fixture
def text_part():
    return DocumentPart(0, None, "text/plain", ExtractionPartKind.NATIVE_TEXT, "Alpha 12,50 7.00", "text", "digest")


def test_compatibility_aliases_and_failure_retain_evidence(text_part):
    assert (DocumentSource, DocumentResult, DocumentPipelineError) == (Source, Result, PipelineError)
    usage = {"input_tokens": 4}
    error = PipelineError("Unresolved", parts=[text_part], stage="mapping", code="invalid", usage_delta=usage)
    usage["input_tokens"] = 9
    assert error.parts == (text_part,)
    assert error.usage_delta == {"input_tokens": 4}


def test_profile_resolves_through_existing_registry_and_fails_closed(settings):
    settings.ANGEE_EXTRACTION_PROFILE_CLASSES = {
        "none": "angee.workflows_extraction.profiles.UnconfiguredExtractionProfile",
    }
    profile_class = resolve_impl_class("ANGEE_EXTRACTION_PROFILE_CLASSES", "none", ExtractionProfile)
    assert profile_class is UnconfiguredExtractionProfile
    with pytest.raises(ValueError, match="Select a document extraction profile"):
        profile_class().process_parts([], [], {}, config={})
    assert profile_class().inference_required({}, ["unresolved"])
    assert not profile_class().inference_required({}, [])
    assert authored_profile_config({"mode": "strict", "retry_of_revision": 3}) == {"mode": "strict"}


def test_claims_use_exact_text_spans_and_escaped_pointers(text_part):
    parts = [text_part, replace(text_part, value={"label": "Alpha"})]
    claims = derive_text_claims({"a/b~": ["Alpha", Decimal("12.5"), 7], "present": True, "absent": None}, parts)
    assert claims == {
        "/a~1b~0/0": [{"part_position": 0, "start": 0, "end": 5}],
        "/a~1b~0/1": [{"part_position": 0, "start": 6, "end": 11}],
        "/a~1b~0/2": [{"part_position": 0, "start": 12, "end": 16}],
    }
    assert derive_text_claims("Alpha", parts) == {"": [{"part_position": 0, "start": 0, "end": 5}]}
    assert derive_text_claims({"code": "7", "partial": 12}, parts) == {}
    assert derive_text_claims({"number": 12}, [replace(text_part, value="A12 112 12/9")]) == {}


def test_pointer_distinguishes_missing_null_root_and_escaped_keys():
    value = {"a/b": {"~": [None]}, "-": "retained"}
    assert json_pointer_value(value, "") is value
    assert json_pointer_value(value, "/a~1b/~0/0") is None
    assert json_pointer_value(value, "/-") == "retained"
    assert json_pointer_value_or_missing(value, "/absent") is JSON_POINTER_MISSING


@pytest.mark.parametrize(
    "pointer", ["rows", "/rows/-", "/rows/00", "/rows/١", "/rows/-1", "/rows/2", "/rows/0/0", "/~2"]
)
def test_pointer_rejects_invalid_paths_and_string_indexing(pointer):
    value = {"rows": ["abc"]}
    with pytest.raises(KeyError):
        json_pointer_value(value, pointer)
    assert json_pointer_value_or_missing(value, pointer) is JSON_POINTER_MISSING


def test_pointer_baseline_proves_complete_array_element_and_container_type():
    current = {"rows": [{"label": "Alpha", "flag": True}]}
    assert json_pointer_value_or_missing(current, "/rows/0/label", array_element_baseline=current) == "Alpha"
    for baseline in (
        {"rows": [{"label": "Alpha", "flag": 1}]},
        {"rows": [{"label": "Alpha", "flag": False}]},
        {"rows": {"0": current["rows"][0]}},
    ):
        assert (
            json_pointer_value_or_missing(current, "/rows/0/label", array_element_baseline=baseline)
            is JSON_POINTER_MISSING
        )


def test_pointer_set_reuses_native_escaping_without_array_append():
    value = {"a/b": ["Alpha"]}
    set_json_pointer(value, "/a~1b/0", "Beta")
    set_json_pointer(value, "/new", None)
    assert value == {"a/b": ["Beta"], "new": None}
    for pointer in ("", "/a~1b/-", "/a~1b/1", "/a~1b/00", "/a~1b/0/0"):
        with pytest.raises(KeyError):
            set_json_pointer(value, pointer, "Gamma")


def test_missing_pointer_path_copies_source_and_requires_contiguous_arrays():
    source = {"rows": [{"details": {"label": "Alpha"}}]}
    value = {"rows": []}
    materialize_missing_json_pointer_path(
        value, "/rows/0/details/label", source=source, source_pointer="/rows/0/details/label"
    )
    assert value == source
    value["rows"][0]["details"]["label"] = "Beta"
    assert source["rows"][0]["details"]["label"] == "Alpha"
    for destination in ("/rows/2/details/label", "/rows/-/details/label"):
        with pytest.raises(KeyError):
            materialize_missing_json_pointer_path(
                {"rows": []}, destination, source=source, source_pointer="/rows/0/details/label"
            )


def test_layout_validates_structure_and_requires_explicit_root_fallback():
    layout = {"document_collection": "/documents", "line_collection": "/rows"}
    assert result_selectors({"documents": [{"rows": [{}, {}]}]}, layout) == (
        ("/documents/0", ("/documents/0/rows/0", "/documents/0/rows/1")),
    )
    with pytest.raises(ValidationError, match="absent"):
        result_selectors({"label": "Alpha"}, layout)
    assert result_selectors({"label": "Alpha"}, layout | {"root_document_on_missing": True}) == (("", ()),)
    for invalid in ({"line_collection": "/~2"}, {"root_document_on_missing": 1}):
        with pytest.raises(ValidationError):
            result_selectors({"label": "Alpha"}, invalid)


def test_identity_correspondence_does_not_guess_reordered_line_identity():
    layout = {"line_collection": "/rows"}
    previous = {"label": "Alpha", "rows": [{"value": "A"}, {"value": "B"}]}
    original = SimpleNamespace(
        result=previous,
        document_refs=(DocumentRef("document", "", (LineRef("first", "/rows/0"), LineRef("second", "/rows/1"))),),
    )
    assert implicit_identity_correspondence(previous | {"label": "Beta"}, layout=layout, original=original) == {
        "": "document",
        "/rows/0": "first",
        "/rows/1": "second",
    }
    assert (
        implicit_identity_correspondence(
            previous | {"rows": list(reversed(previous["rows"]))}, layout=layout, original=original
        )
        is None
    )
