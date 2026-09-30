"""A held candidate cannot silently adopt retained document or line identities."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError

from angee.extraction.contracts import ExtractionPartKind, PipelineError
from angee.extraction.enums import ExtractionErrorCode
from tests.extraction_models import Extraction
from tests.test_extraction_models import evidence as evidence
from tests.test_storage import drive as drive


def test_repeated_held_candidate_keeps_successful_authority_until_correspondence(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    candidate = deepcopy(values["result"].value)
    candidate["documents"][0]["lines"].reverse()
    result = replace(values["result"], value=candidate)
    held = retain(base=first, request_key="held", result=result)
    assert held.awaiting_correspondence
    assert held.fact("/documents/0/lines/0/text") == "Second line"
    with pytest.raises(ValueError, match="requires correspondence"):
        held.selected_document(reference.identity)
    with pytest.raises(ValueError, match="requires correspondence"):
        held.selected_line(reference.identity, reference.lines[0].identity)

    repeated = retain(base=held, request_key="repeated-held", result=result)
    assert repeated.awaiting_correspondence
    assert repeated.outcome["identity_correspondence"] == {
        "last_known_revision": first.revision,
        "expected_base_id": str(first.sqid),
    }
    resolved = retain(
        base=repeated,
        request_key="resolved",
        result=result,
        identity_mapping={
            reference.selector: reference.identity,
            reference.lines[0].selector: reference.lines[1].identity,
            reference.lines[1].selector: reference.lines[0].identity,
        },
    )
    assert resolved.outcome["kind"] == "succeeded"
    assert resolved.selected_line(reference.identity, reference.lines[0].identity)[0]["text"] == "First line"


def test_retired_line_and_absent_fact_do_not_resolve_to_a_surviving_position(evidence):
    retain, values = evidence
    first = retain()
    reference = first.document_refs[0]
    candidate = deepcopy(values["result"].value)
    candidate["documents"][0]["lines"].pop(0)
    candidate["documents"][0].pop("optional")
    resolved = retain(
        base=first,
        request_key="retired",
        result=replace(values["result"], value=candidate),
        identity_mapping={
            reference.selector: reference.identity,
            reference.lines[0].selector: reference.lines[1].identity,
        },
        retired_identities={reference.lines[0].identity: "The first line is absent"},
    )
    with pytest.raises(KeyError):
        resolved.selected_line(reference.identity, reference.lines[0].identity)
    with pytest.raises(KeyError):
        resolved.fact("/documents/0/optional")
    assert resolved.selected_line(reference.identity, reference.lines[1].identity)[0]["text"] == "Second line"


@pytest.mark.parametrize("failure_stage", ["inference", "correspondence"])
def test_correction_binding_freezes_direct_or_failed_parent(evidence, failure_stage):
    retain, values = evidence
    first = retain()
    binding, parent = Extraction.objects.prepare_correction_binding(first, actor=values["actor"])
    assert parent.pk == first.pk and not binding.bridges_revision_parent
    failed = retain(
        base=first,
        request_key="failed-inference",
        result=replace(values["result"], value={}, claims={}),
        failure=PipelineError("Inference failed", stage="inference", code="unavailable")
        if failure_stage == "inference"
        else None,
        error_code=ExtractionErrorCode.IDENTITY_CORRESPONDENCE_REQUIRED if failure_stage == "correspondence" else None,
    )
    binding, parent = Extraction.objects.prepare_correction_binding(first, actor=values["actor"])
    assert binding.authority == first.reference
    assert binding.revision_parent == failed.reference
    assert parent.pk == failed.pk and binding.bridges_revision_parent
    retain(base=failed, request_key="successful-successor")
    with pytest.raises(ValidationError, match="another retained fact authority"):
        Extraction.objects.prepare_correction_binding(first, actor=values["actor"])


def test_correction_binding_rejects_nonempty_correspondence_candidate(evidence):
    retain, values = evidence
    first = retain()
    candidate = deepcopy(values["result"].value)
    candidate["documents"][0]["lines"].reverse()
    held = retain(base=first, request_key="held-for-binding", result=replace(values["result"], value=candidate))
    assert held.awaiting_correspondence
    with pytest.raises(ValidationError, match="another retained fact authority"):
        Extraction.objects.prepare_correction_binding(first, actor=values["actor"])


@pytest.mark.parametrize("omit", [False, True])
def test_inference_retention_preserves_source_grounding_before_schema_validation(evidence, omit):
    retain, values = evidence
    first = retain()
    candidate = deepcopy(first.result)
    if omit:
        candidate["documents"][0].pop("title")
    else:
        candidate["documents"][0]["title"] = "Invented"
    candidate["documents"][0]["optional"] = "Inferred"
    result = replace(values["result"], value=candidate, claims={})
    successor = retain(base=first, result=result, request_key="preserved")
    assert successor.outcome["kind"] == "succeeded"
    assert successor.fact("/documents/0/title") == "Note"
    assert successor.fact("/documents/0/optional") == "Inferred"
    assert successor.claims == first.claims
    assert successor.fact_authority("/documents/0/title").kind == "source"
    assert retain(base=first, result=result, request_key="preserved").pk == successor.pk


def test_inference_retention_rejects_replacement_carriers(evidence):
    retain, values = evidence
    first = retain()
    changed = replace(values["result"].parts[0], value="Rewritten source")
    with pytest.raises(ValidationError, match="cannot replace retained evidence parts"):
        retain(base=first, request_key="rewritten", result=replace(values["result"], parts=(changed,)))
    assert Extraction.objects.count() == 1


@pytest.mark.parametrize(("original", "replacement"), [(True, 1), (1, 1.0)])
def test_exact_retained_carriers_preserve_json_scalar_types(evidence, original, replacement):
    retain, values = evidence
    part = replace(values["result"].parts[0], kind=ExtractionPartKind.STRUCTURED, value={"flag": original})
    result = replace(values["result"], parts=(part,))
    first = retain(result=result)
    with pytest.raises(ValidationError, match="cannot replace retained evidence parts"):
        retain(
            base=first,
            request_key="changed-json-type",
            result=replace(result, parts=(replace(part, value={"flag": replacement}),)),
        )
