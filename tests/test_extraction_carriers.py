"""Authoritative claims follow unique physical carriers across source reordering."""

from copy import deepcopy
from dataclasses import replace

import pytest
from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from angee.extraction.contracts import DocumentPart, DocumentSource, ExtractionPartKind
from angee.graphql.publishing import mute_changes
from tests.conftest import create_platform_admin
from tests.messaging_models import Part
from tests.test_extraction_models import evidence as evidence
from tests.test_messaging_part_tree import part_tree as part_tree
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


def message_source_and_part(part, position):
    """Describe one message part as an input and its acquired text carrier."""
    text = part.fragment.text
    digest = part.fragment.hash
    return (
        DocumentSource(position, digest, part.type, text, message_part=part),
        DocumentPart(position, 0, part.type, ExtractionPartKind.NATIVE_TEXT, text, "native", digest),
    )


@pytest.fixture
def message_evidence(part_tree, evidence):
    retain, values = evidence
    message, parts = part_tree
    original_source, original_part = message_source_and_part(parts["plain"], 0)
    document = deepcopy(values["result"].value)
    document["documents"][0]["title"] = "Plain"
    result = replace(
        values["result"],
        value=document,
        parts=(original_part,),
        claims={"/documents/0/title": [{"part_position": 0, "start": 0, "end": 5}]},
    )
    values = {
        **values,
        "target": message,
        "actor": create_platform_admin("carrier-author"),
        "sources": (original_source,),
        "result": result,
    }

    def retain_message(**changes):
        return retain(**{**values, **changes})

    with actor_context(values["actor"]):
        yield retain_message, values, parts


def test_reordered_sources_keep_physical_authority_and_remap_claim_positions(message_evidence):
    retain, values, parts = message_evidence
    original = values["sources"][0]
    extra_source, extra_part = message_source_and_part(parts["forwarded_plain"], 1)
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
    assert held.awaiting_correspondence and held.lineage_id == first.lineage_id
    assert first.authority_carrier_positions(held) == {0: 1, 1: 0}
    resolved = retain(
        base=held,
        request_key="reordered-resolved",
        sources=(),
        result=candidate,
        identity_mapping=reviewed_mapping(first),
    )
    assert resolved.result["documents"][0]["title"] == "Plain"
    assert resolved.claims["/documents/0/title"] == [{"part_position": 1, "start": 0, "end": 5}]


def test_identical_text_from_another_message_part_cannot_inherit_authority(message_evidence):
    retain, values, parts = message_evidence
    first = retain()
    original = values["sources"][0]
    with system_context(reason="tests.extraction distinct carrier sources"), mute_changes():
        replacement = Part.objects.create(
            message=values["target"],
            position=2,
            type="text/plain",
            role="body",
            fragment=parts["plain"].fragment,
        )
    replacement_source, replacement_part = message_source_and_part(replacement, 1)
    assert replacement.pk != original.message_part.pk and replacement_source.content_hash == original.content_hash
    candidate = replace(
        values["result"],
        value=reversed_candidate(values),
        claims={},
        parts=(replacement_part,),
    )
    held = retain(request_key="replacement-hold", sources=(original, replacement_source), result=candidate)
    assert held.awaiting_correspondence and held.lineage_id == first.lineage_id
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
