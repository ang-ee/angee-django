"""Composed persistence tests for document extraction evidence."""

from __future__ import annotations

import hashlib
import io
import tempfile
from collections.abc import Mapping
from contextlib import nullcontext
from copy import copy, deepcopy
from types import SimpleNamespace
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, models, transaction
from django.test import SimpleTestCase, override_settings
from rebac import (
    MissingActorError,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.messaging.backends import ParsedMessage, ParsedPart
from angee.workflows_extraction.engines import (
    RETAINED_AUTHORITY_COMPLETION_REVIEW,
    RETAINED_CARRIER_UNAVAILABLE,
    DocumentPart,
    DocumentPipelineError,
    DocumentResult,
    DocumentSource,
    InferenceMappingEngine,
    PageImage,
    derive_text_claims,
)
from angee.workflows_extraction.managers import _document_mapping
from angee.workflows_extraction.models import DocumentRef, LineRef
from angee.workflows_extraction.pointers import (
    implicit_identity_correspondence,
    json_pointer_value,
    result_selectors,
)
from angee.workflows_extraction.routing import (
    _decode_declared_text,
    _html_text,
    recognize_pages,
)
from angee.workflows_extraction.service import (
    PreparedDocument,
    PreparedPage,
    _document_sources,
    _preserve_retained_authority,
    _retained_claim_part_positions,
    _reviewed_correction_unresolved_reasons,
    _unchanged_claims,
    collect_carriers,
    infer,
    prepare_pages,
    process,
    require_approved_model_deployment,
)
from angee.workflows_extraction.service import (
    revise as retain_revision,
)
from angee.workflows_extraction.steps import ExtractionConfig, InferEvidenceStepImpl
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.extraction_models import EXTRACTION_MODELS, Extraction, ExtractionPage, ExtractionSource
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS
from tests.test_integrate_vcs import VCS_TEST_MODELS
from tests.test_messaging import MESSAGING_TEST_MODELS
from tests.workflows import Decision, Step, StepRun, Workflow, WorkflowRun


def test_json_pointer_value_resolves_rfc6901_tokens_and_rejects_missing() -> None:
    assert json_pointer_value({"vendor/name": {"tax~id": "CZ123"}}, "/vendor~1name/tax~0id") == "CZ123"
    with pytest.raises(KeyError):
        json_pointer_value({"vendor": {}}, "/vendor/name")


def test_inference_provider_failure_routes_retained_base_to_manual_review() -> None:
    actor = object()
    target = SimpleNamespace(pk=11)
    base_manager = SimpleNamespace()
    extraction_fixture = type("ExtractionFixture", (), {"objects": base_manager})
    base = extraction_fixture()
    for name, value in {
        "pk": 7,
        "sqid": "ext_base",
        "revision": 3,
        "status": "succeeded",
        "error_code": "",
        "unresolved_reasons": ["missing_supplier_identity"],
        "result": {"invoice_count": 1, "routing_review_reasons": ["missing_supplier_identity"]},
        "corrections": (),
        "provenance": {"used_model_roles": []},
        "engine": "invoice_document",
        "content_type_id": 5,
        "object_id": target.pk,
    }.items():
        setattr(base, name, value)
    base_manager.get = lambda **_kwargs: base
    base_manager.inference_current_head = lambda _base, *, actor: base
    extraction_model = SimpleNamespace(objects=base_manager)
    target_model = SimpleNamespace(objects=SimpleNamespace(get=lambda **_kwargs: target))
    model = object()
    inference_model = SimpleNamespace(objects=SimpleNamespace(get=lambda **_kwargs: model))
    models = {
        ("workflows_extraction", "Extraction"): extraction_model,
        ("storage", "File"): target_model,
        ("agents", "InferenceModel"): inference_model,
    }
    step_run = SimpleNamespace(
        run=SimpleNamespace(admission_actor=lambda: actor),
    )
    request = SimpleNamespace(input={
        "base_extraction_id": "ext_base",
        "base_revision": 3,
        "model_id": "imd_mapping",
        "target_model": "storage.File",
        "target_id": "fil_source",
        "identity_mapping": {},
        "retired_identities": {},
    })

    class Profile:
        def inference_required(self, _result, _reasons):
            return True

    with (
        patch("angee.workflows_extraction.steps.external_operation_request", return_value=request),
        patch(
            "angee.workflows_extraction.steps.apps.get_model",
            side_effect=lambda *key: models[
                tuple(key[0].split(".", 1)) if len(key) == 1 else key
            ],
        ),
        patch("angee.workflows_extraction.steps.actor_context", side_effect=lambda _actor: nullcontext()),
        patch("angee.workflows_extraction.steps.canonical_record_target", return_value=SimpleNamespace(
            content_type=SimpleNamespace(pk=5), object_id=target.pk,
        )),
        patch("angee.workflows_extraction.steps.resolve_impl_class", return_value=Profile),
        patch("angee.workflows_extraction.steps.infer", side_effect=DocumentPipelineError(
            "The inferred candidate does not match the frozen schema.",
            stage="inference",
            code="candidate_schema_mismatch",
        )),
    ):
        result = InferEvidenceStepImpl().run(step_run, now=None)

    assert result.kind == "done"
    assert result.outcome == "inference_failed"
    assert result.output["extraction_id"] == "ext_base"
    assert result.output["inference_failure"] == {
        "type": "DocumentPipelineError",
        "message": "The inferred candidate does not match the frozen schema.",
        "stage": "inference",
        "code": "candidate_schema_mismatch",
    }


def test_retained_carrier_mismatch_routes_exact_hold_and_authority_without_relabeling() -> None:
    actor = object()
    target = SimpleNamespace(pk=11)
    old_part = SimpleNamespace(sqid="prt_original")
    duplicate_part = SimpleNamespace(sqid="prt_duplicate")
    authority_sources = (
        DocumentSource(0, "a" * 64, "text/plain", "same body", message_part=old_part),
    )
    current_sources = (
        DocumentSource(0, "a" * 64, "text/plain", "same body", message_part=duplicate_part),
    )
    authority_parts = (
        DocumentPart(0, None, "text/plain", "native_text", "same body", "native", "b" * 64),
    )
    current_parts = tuple([*authority_parts])
    base_manager = SimpleNamespace()
    extraction_fixture = type("ExtractionFixture", (), {"objects": base_manager})
    authority = extraction_fixture()
    for name, value in {
        "pk": 7,
        "sqid": "ext_authority",
        "revision": 2,
        "status": "succeeded",
        "error_code": "",
        "unresolved_reasons": [],
        "claims": {"/number": [{"part_position": 0, "start": 0, "end": 4}]},
        "document_refs": (),
        "result": {"invoice_count": 1},
        "corrections": (),
        "provenance": {"used_model_roles": []},
        "engine": "invoice_document",
        "content_type_id": 5,
        "object_id": target.pk,
    }.items():
        setattr(authority, name, value)
    retained_claims = deepcopy(authority.claims)
    with pytest.raises(DocumentPipelineError) as mismatch:
        _retained_claim_part_positions(
            authority,
            authority_sources=authority_sources,
            authority_parts=authority_parts,
            current_sources=current_sources,
            current_parts=current_parts,
            retired_identities={},
        )
    assert mismatch.value.stage == "correspondence"
    assert mismatch.value.code == RETAINED_CARRIER_UNAVAILABLE

    hold = extraction_fixture()
    for name, value in {
        "pk": 8,
        "sqid": "ext_hold",
        "revision": 4,
        "status": "failed",
        "error_code": "source_hold:identity_correspondence_required",
        "unresolved_reasons": ["identity_correspondence_required"],
        "result": {"invoice_count": 1},
        "corrections": (),
        "provenance": {"used_model_roles": []},
        "engine": "invoice_document",
        "content_type_id": 5,
        "object_id": target.pk,
    }.items():
        setattr(hold, name, value)
    selected = [hold]
    base_manager.get = lambda **_kwargs: selected[0]
    base_manager.inference_current_head = lambda base, *, actor: base
    base_manager.inference_authority_base = lambda base, *, actor: (
        authority if base is hold else base
    )
    models = {
        ("workflows_extraction", "Extraction"): SimpleNamespace(objects=base_manager),
        ("storage", "File"): SimpleNamespace(objects=SimpleNamespace(get=lambda **_kwargs: target)),
        ("agents", "InferenceModel"): SimpleNamespace(
            objects=SimpleNamespace(get=lambda **_kwargs: object())
        ),
    }
    request = SimpleNamespace(input={
        "base_extraction_id": "ext_hold",
        "base_revision": 4,
        "model_id": "imd_mapping",
        "target_model": "storage.File",
        "target_id": "fil_source",
        "identity_mapping": {},
        "retired_identities": {},
    })

    class Profile:
        def inference_required(self, _result, _reasons):
            return True

    with (
        patch("angee.workflows_extraction.steps.external_operation_request", return_value=request),
        patch(
            "angee.workflows_extraction.steps.apps.get_model",
            side_effect=lambda *key: models[tuple(key[0].split(".", 1)) if len(key) == 1 else key],
        ),
        patch("angee.workflows_extraction.steps.actor_context", side_effect=lambda _actor: nullcontext()),
        patch(
            "angee.workflows_extraction.steps.canonical_record_target",
            return_value=SimpleNamespace(content_type=SimpleNamespace(pk=5), object_id=target.pk),
        ),
        patch("angee.workflows_extraction.steps.resolve_impl_class", return_value=Profile),
        patch("angee.workflows_extraction.steps.infer", side_effect=mismatch.value),
    ):
        result = InferEvidenceStepImpl().run(
            SimpleNamespace(run=SimpleNamespace(admission_actor=lambda: actor)),
            now=None,
        )

    assert result.outcome == "source_unavailable"
    assert result.output["extraction_id"] == "ext_hold"
    assert result.output["inference_failure"]["code"] == RETAINED_CARRIER_UNAVAILABLE
    assert [(artifact.target, artifact.label) for artifact in result.artifacts] == [
        (hold, "Current source evidence"),
        (authority, "Original retained evidence"),
    ]
    assert authority.claims == retained_claims

    selected[0] = authority
    request.input["base_extraction_id"] = "ext_authority"
    request.input["base_revision"] = 2
    with (
        patch("angee.workflows_extraction.steps.external_operation_request", return_value=request),
        patch(
            "angee.workflows_extraction.steps.apps.get_model",
            side_effect=lambda *key: models[tuple(key[0].split(".", 1)) if len(key) == 1 else key],
        ),
        patch("angee.workflows_extraction.steps.actor_context", side_effect=lambda _actor: nullcontext()),
        patch(
            "angee.workflows_extraction.steps.canonical_record_target",
            return_value=SimpleNamespace(content_type=SimpleNamespace(pk=5), object_id=target.pk),
        ),
        patch("angee.workflows_extraction.steps.resolve_impl_class", return_value=Profile),
        patch("angee.workflows_extraction.steps.infer", side_effect=mismatch.value),
    ):
        ordinary = InferEvidenceStepImpl().run(
            SimpleNamespace(run=SimpleNamespace(admission_actor=lambda: actor)),
            now=None,
        )

    assert ordinary.outcome == "inference_failed"
    assert [(artifact.target, artifact.label) for artifact in ordinary.artifacts] == [
        (authority, "Source evidence requiring manual review"),
    ]


def test_inference_retains_disabled_base_and_routes_current_correspondence() -> None:
    actor = object()
    target = SimpleNamespace(pk=11)
    base_manager = SimpleNamespace()
    extraction_fixture = type("ExtractionFixture", (), {"objects": base_manager})
    base = extraction_fixture()
    successor = extraction_fixture()
    for name, value in {
        "pk": 7,
        "sqid": "ext_base",
        "revision": 3,
        "status": "succeeded",
        "error_code": "",
        "unresolved_reasons": ["missing_supplier_identity"],
        "content_type_id": 5,
        "object_id": target.pk,
    }.items():
        setattr(base, name, value)
    successor.pk = 8
    successor.sqid = "ext_successor"
    successor.revision = 4
    successor.status = "failed"
    successor.error_code = "source_hold:identity_correspondence_required"
    successor.unresolved_reasons = ["identity_correspondence_required"]
    empty_successor = extraction_fixture()
    empty_successor.pk = 9
    empty_successor.sqid = "ext_empty_successor"
    empty_successor.revision = 4
    empty_successor.status = "failed"
    empty_successor.error_code = "source_hold:identity_correspondence_required"
    empty_successor.unresolved_reasons = ["identity_correspondence_required"]
    base_manager.get = lambda **_kwargs: base
    current = base
    base_manager.inference_current_head = lambda _base, *, actor: current
    base_manager.inference_authority_base = lambda _base, *, actor: base
    base_manager.inference_candidate_selectors = lambda extraction: (
        () if extraction is empty_successor else (("/documents/0", ()),)
    )
    models = {
        ("workflows_extraction", "Extraction"): SimpleNamespace(objects=base_manager),
        ("storage", "File"): SimpleNamespace(
            objects=SimpleNamespace(get=lambda **_kwargs: target)
        ),
    }
    step_run = SimpleNamespace(run=SimpleNamespace(admission_actor=lambda: actor))
    request = SimpleNamespace(
        input={
            "base_extraction_id": "ext_base",
            "base_revision": 3,
            "allow_inference": False,
            "model_id": "imd_mapping",
            "target_model": "storage.File",
            "target_id": "fil_source",
            "identity_mapping": {},
            "retired_identities": {},
        }
    )

    with (
        patch(
            "angee.workflows_extraction.steps.external_operation_request",
            return_value=request,
        ),
        patch(
            "angee.workflows_extraction.steps.apps.get_model",
            side_effect=lambda *key: models[
                tuple(key[0].split(".", 1)) if len(key) == 1 else key
            ],
        ),
        patch(
            "angee.workflows_extraction.steps.actor_context",
            side_effect=lambda _actor: nullcontext(),
        ),
        patch(
            "angee.workflows_extraction.steps.canonical_record_target",
            return_value=SimpleNamespace(
                content_type=SimpleNamespace(pk=5), object_id=target.pk
            ),
        ),
        patch("angee.workflows_extraction.steps.resolve_impl_class") as resolve_profile,
        patch("angee.workflows_extraction.steps.infer") as infer_call,
    ):
        result = InferEvidenceStepImpl().run(step_run, now=None)
        current = successor
        retained_correspondence = InferEvidenceStepImpl().run(step_run, now=None)
        base.status = "failed"
        with pytest.raises(
            ValidationError, match="Retained-only inference requires successful evidence"
        ):
            InferEvidenceStepImpl().run(step_run, now=None)
        base.status = "succeeded"
        request.input["allow_inference"] = True
        correspondence = InferEvidenceStepImpl().run(step_run, now=None)
        current = empty_successor
        empty_correspondence = InferEvidenceStepImpl().run(step_run, now=None)

    assert result.kind == "done"
    assert result.outcome == "unchanged"
    assert result.output == {
        "extraction_id": "ext_base",
        "revision": 3,
        "status": "succeeded",
        "error_code": "",
        "unresolved_reasons": ["missing_supplier_identity"],
    }
    resolve_profile.assert_not_called()
    infer_call.assert_not_called()
    assert retained_correspondence.kind == "done"
    assert retained_correspondence.outcome == "correspondence_required"
    assert retained_correspondence.output == {
        "extraction_id": "ext_successor",
        "revision": 4,
        "status": "failed",
        "error_code": "source_hold:identity_correspondence_required",
        "unresolved_reasons": ["identity_correspondence_required"],
    }
    assert correspondence.kind == "done"
    assert correspondence.outcome == "correspondence_required"
    assert correspondence.output == {
        "extraction_id": "ext_successor",
        "revision": 4,
        "status": "failed",
        "error_code": "source_hold:identity_correspondence_required",
        "unresolved_reasons": ["identity_correspondence_required"],
    }
    assert empty_correspondence.kind == "done"
    assert empty_correspondence.outcome == "inference_failed"
    assert empty_correspondence.output == {
        "extraction_id": "ext_base",
        "revision": 3,
        "status": "succeeded",
        "error_code": "",
        "unresolved_reasons": ["missing_supplier_identity"],
        "inference_failure": {
            "type": "DocumentPipelineError",
            "message": "The retained correspondence candidate is empty.",
            "stage": "correspondence",
            "code": "empty_correspondence_candidate",
        },
    }
    assert [artifact.target for artifact in empty_correspondence.artifacts] == [
        empty_successor,
        base,
    ]
    infer_call.assert_not_called()


def test_retained_authority_materializes_only_missing_claimed_containers() -> None:
    base = SimpleNamespace(
        claims={
            "/currency": [{"part_position": 0}],
            "/source_payment_claims/0/printed_text": [{"part_position": 0}],
        },
        corrections=(),
        document_refs=(),
        result={
            "reference": "SOURCE-1",
            "currency": "USD",
            "source_payment_claims": [{
                "kind": "unresolved",
                "printed_text": "Payment status: not paid",
                "amount": None,
                "currency": "",
            }],
        },
    )

    result, claims, completion_required = _preserve_retained_authority(
        base,
        {
            "supplier": "Provider candidate",
            "source_payment_claims": [],
        },
        {"/supplier": [{"part_position": 0}]},
        identity_mapping={},
        retired_identities={},
        claim_part_positions={0: 0},
    )

    assert result == {
        "supplier": "Provider candidate",
        "currency": "USD",
        "source_payment_claims": [{
            "kind": "unresolved",
            "printed_text": "Payment status: not paid",
            "amount": None,
            "currency": "",
        }],
    }
    assert "reference" not in result
    assert completion_required is True
    assert _reviewed_correction_unresolved_reasons(
        {
            "unresolved_reasons": [
                "retained_carrier_hold",
                RETAINED_AUTHORITY_COMPLETION_REVIEW,
            ]
        }
    ) == ["retained_carrier_hold"]
    assert claims == {
        "/supplier": [{"part_position": 0}],
        "/currency": [{"part_position": 0}],
        "/source_payment_claims/0/printed_text": [{"part_position": 0}],
    }


@pytest.fixture()
def extraction_tables(transactional_db):
    """Use the same concrete model graph as messaging, agents, and stored files."""

    models = tuple(
        dict.fromkeys((*MESSAGING_TEST_MODELS, *VCS_TEST_MODELS, *AGENTS_GRAPHQL_MODELS, *EXTRACTION_MODELS))
    )
    _create_missing_tables(models)
    try:
        yield
    finally:
        _clear_model_tables(models)


SCHEMA = {
    "$id": "test.synthetic.document.v1",
    "type": "object",
    "properties": {
        "number": {"type": "string"},
        "rows": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["number", "rows"],
    "additionalProperties": False,
}


class PageAggregationTests(SimpleTestCase):
    def test_missing_recognition_keeps_native_page_and_explicitly_holds_scanned_page(self) -> None:
        source = SimpleNamespace(
            source_position=0, file=SimpleNamespace(sqid="fil_source"), message_part=None, content_hash="source"
        )
        native = DocumentPart(0, 0, "text/plain", "native_text", "printed text", "pdf_text", "native")
        raster = PageImage(0, 1, "image/jpeg", b"synthetic-image", 200, 100, 200)
        image_file = SimpleNamespace(sqid="fil_image", content_hash="image", upload_state="ready")
        prepared = PreparedDocument(
            (source,),
            (PreparedPage(0, 0, (native,), ()), PreparedPage(0, 1, (), (image_file,), raster, image_file)),
        )
        collected = collect_carriers(prepared, [])
        self.assertEqual(collected.parts, (native,))
        self.assertEqual([(page.source_position, page.page_position) for page in collected.pages], [(0, 0), (0, 1)])
        self.assertEqual([result.value["status"] for result in collected.page_results], ["native", "held"])
        self.assertEqual(collected.hold_reasons, ("recognition_page_0_1_missing",))

    def test_declared_empty_collection_and_reviewed_split_identity(self) -> None:
        layout = {"document_collection": "/items", "line_collection": "/rows"}
        self.assertEqual(result_selectors({"items": []}, layout), ())
        with self.assertRaisesMessage(ValidationError, "collection is absent"):
            result_selectors({"other": []}, layout)

        first_lines = implicit_identity_correspondence(
            {"rows": [{"amount": 10}, {"amount": 20}]},
            layout={"line_collection": "/rows"},
            original=SimpleNamespace(
                result={"rows": []},
                document_refs=(DocumentRef("root-id", ""),),
            ),
        )
        self.assertEqual(
            first_lines,
            {"": "root-id", "/rows/0": "new", "/rows/1": "new"},
        )
        self.assertEqual(
            result_selectors({"other": "root"}, {**layout, "root_document_on_missing": True}),
            (("", ()),),
        )
        with self.assertRaisesMessage(ValidationError, "explicitly mapped"):
            _document_mapping(
                {"items": [{"label": "reclassified"}]},
                layout=layout,
                original=SimpleNamespace(
                    result={"label": "root"},
                    document_refs=(DocumentRef("root-id", ""),),
                ),
                identity_mapping={},
                retired_identities={},
            )

        original = SimpleNamespace(
            result={
                "items": [
                    {"label": "A", "rows": [{"amount": 10}, {"amount": 20}]},
                    {"label": "B", "rows": [{"amount": 30}]},
                ]
            },
            document_refs=(
                DocumentRef(
                    "doc-a",
                    "/items/0",
                    (
                        LineRef("line-a1", "/items/0/rows/0"),
                        LineRef("line-a2", "/items/0/rows/1"),
                    ),
                ),
                DocumentRef("doc-b", "/items/1", (LineRef("line-b", "/items/1/rows/0"),)),
            ),
        )
        rows, retired = _document_mapping(
            {
                "items": [
                    {"label": "B corrected", "rows": [{"amount": 31}]},
                    {"label": "A corrected", "rows": [{"amount": 11}]},
                    {"label": "C split", "rows": [{"amount": 40}]},
                ]
            },
            layout=layout,
            original=original,
            identity_mapping={
                "/items/0": "doc-b",
                "/items/0/rows/0": "line-b",
                "/items/1": "doc-a",
                "/items/1/rows/0": "line-a1",
                "/items/2": "new",
                "/items/2/rows/0": "new",
            },
            retired_identities={"line-a2": "Reviewed line retirement after split"},
        )
        self.assertEqual([row["identity"] for row in rows[:2]], ["doc-b", "doc-a"])
        self.assertNotIn(rows[2]["identity"], {"doc-a", "doc-b", "line-a2"})
        self.assertEqual([row["lines"][0]["identity"] for row in rows[:2]], ["line-b", "line-a1"])
        self.assertEqual(
            retired,
            [
                {
                    "identity": "line-a2",
                    "kind": "line",
                    "reason": "Reviewed line retirement after split",
                }
            ],
        )

    def test_derives_claim_spans_only_for_values_present_in_retained_text(self) -> None:
        parts = (DocumentPart(0, 0, "text/plain", "native_text", "Invoice 22121 total 174.20", "test", "a" * 64),)
        claims = derive_text_claims({"reference": "22121", "total": "174.20", "bank": "invented"}, parts)
        self.assertEqual(set(claims), {"/reference", "/total"})
        self.assertEqual(claims["/reference"][0], {"part_position": 0, "start": 8, "end": 13})

    def test_claim_spans_exclude_empty_and_partial_numeric_matches(self) -> None:
        text = "Postal 00601 invoice INV-1 quantity 1 price 87.10"
        part = DocumentPart(0, 0, "text/plain", "native_text", text, "test", "a" * 64)
        claims = derive_text_claims(
            {"vendor": {"tax_id": ""}, "quantity": 1, "unit_price": "87.1"},
            (part,),
        )
        self.assertNotIn("/vendor/tax_id", claims)
        self.assertEqual(text[claims["/quantity"][0]["start"] : claims["/quantity"][0]["end"]], "1")
        self.assertEqual(text[claims["/unit_price"][0]["start"] : claims["/unit_price"][0]["end"]], "87.10")

    def test_numeric_scalars_ground_equivalent_whole_tokens_without_coercing_strings(
        self,
    ) -> None:
        text = "Zero 0.00 total 100.00 reference 001 grouped 1,000"
        part = DocumentPart(0, 0, "text/plain", "native_text", text, "test", "a" * 64)

        claims = derive_text_claims(
            {
                "zero": 0,
                "one": 1,
                "total": 100,
                "exact_reference": "001",
                "different_reference": "1",
            },
            (part,),
        )

        self.assertEqual(
            text[claims["/zero"][0]["start"] : claims["/zero"][0]["end"]],
            "0.00",
        )
        self.assertEqual(
            text[claims["/total"][0]["start"] : claims["/total"][0]["end"]],
            "100.00",
        )
        self.assertNotIn("/one", claims)
        self.assertEqual(
            text[
                claims["/exact_reference"][0]["start"]
                : claims["/exact_reference"][0]["end"]
            ],
            "001",
        )
        self.assertNotIn("/different_reference", claims)

    def test_claim_retention_rejects_non_ascii_array_pointer_indices(self) -> None:
        claims = {"/rows/０": [{"part_position": 0}]}
        self.assertEqual(
            _unchanged_claims(claims, before={"rows": ["same"]}, after={"rows": ["same"]}),
            {},
        )

    def test_claim_retention_drops_equal_leaves_under_reordered_array_elements(self) -> None:
        claims = {
            "/currency": [{"part_position": 0}],
            "/documents/0/quantity": [{"part_position": 1}],
            "/documents/1/quantity": [{"part_position": 2}],
            "/documents/2/quantity": [{"part_position": 3}],
        }
        before = {
            "currency": "EUR",
            "documents": [
                {"identity": "A", "quantity": 1},
                {"identity": "B", "quantity": 1},
                {"identity": "C", "quantity": 2},
            ],
        }
        after = {
            "currency": "EUR",
            "documents": [
                {"identity": "B", "quantity": 1},
                {"identity": "A", "quantity": 1},
                {"identity": "C", "quantity": 2},
            ],
        }

        self.assertEqual(
            _unchanged_claims(claims, before=before, after=after),
            {
                "/currency": [{"part_position": 0}],
                "/documents/2/quantity": [{"part_position": 3}],
            },
        )

    def test_missing_models_retain_acquired_evidence(self) -> None:
        part = DocumentPart(0, 0, "text/plain", "native_text", "Invoice 22121", "test", "a" * 64)
        with self.assertRaises(DocumentPipelineError) as mapping_error:
            InferenceMappingEngine().map_text_parts((part,), SCHEMA, model=None, config={}, timeout=1)
        self.assertEqual(mapping_error.exception.parts, (part,))

        page = PageImage(0, 1, "image/jpeg", b"bytes", 10, 10, 200)
        with self.assertRaises(DocumentPipelineError) as recognition_error:
            recognize_pages((page,), engine=object(), model=None, config={}, timeout=1, acquired_parts=(part,))
        self.assertEqual(recognition_error.exception.parts, (part,))

    def test_declared_text_decode_is_bounded_to_utf8_and_html_is_inert(self) -> None:
        self.assertEqual(_decode_declared_text(b"\xef\xbb\xbfInvoice 22121"), "Invoice 22121")
        with self.assertRaises(ValueError):
            _decode_declared_text(b"\xff\xfeI\x00")
        self.assertEqual(
            _html_text("<p>Invoice 22121</p><script>ignore()</script><a href='https://invalid'>Total 10</a>"),
            "Invoice 22121\nTotal 10",
        )

    @override_settings(ANGEE_EXTRACTION_MAX_BYTES=10)
    def test_document_source_rejects_bytes_that_do_not_match_retained_identity(self) -> None:
        opened = 0

        def open_stream():
            nonlocal opened
            opened += 1
            return io.BytesIO(b"real")

        file = SimpleNamespace(
            open_stream=open_stream,
            content_hash="0" * 64,
            size_bytes=4,
            mime_type=SimpleNamespace(mime_type="text/plain"),
        )
        with self.assertRaisesMessage(ValidationError, "no longer matches"):
            _document_sources((file,), ())
        self.assertEqual(opened, 1)

    @override_settings(ANGEE_EXTRACTION_MAX_BYTES=10)
    def test_document_source_allows_missing_advisory_mime_type(self) -> None:
        content = b"<Invoice/>"
        file = SimpleNamespace(
            open_stream=lambda: io.BytesIO(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            mime_type=None,
        )
        source = _document_sources((file,), ())[0]
        self.assertEqual(source.mime_type, "")


@pytest.mark.usefixtures("extraction_tables", "workflow_engine_tables")
class ExtractionServiceTests(TestCase):
    """Exercise the service against the concrete composed runtime models."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.storage_root = tempfile.TemporaryDirectory(prefix="angee-extraction-tests-")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.storage_root.cleanup()
        super().tearDownClass()

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        self.owner = get_user_model().objects.create_user(username="extraction-owner")
        self.stranger = get_user_model().objects.create_user(username="extraction-stranger")
        backend_model = apps.get_model("storage", "Backend")
        drive_model = apps.get_model("storage", "Drive")
        mime_model = apps.get_model("storage", "MimeType")
        vendor_model = apps.get_model("integrate", "Vendor")
        provider_model = apps.get_model("agents", "InferenceProvider")
        inference_model = apps.get_model("agents", "InferenceModel")
        with system_context(reason="workflows_extraction tests setup"):
            mime_model.objects.get_or_create(
                mime_type="text/plain",
                defaults={"category": "document", "label": "Plain text", "icon_key": "file-text"},
            )
            backend = backend_model.objects.create(
                slug="extraction-tests",
                label="Extraction tests",
                backend_class="local",
                backend_config={"root": self.storage_root.name, "base_url": "/test-media/"},
                created_by=self.owner,
            )
            self.drive = drive_model.objects.create(
                backend=backend,
                slug="extraction-tests",
                name="Extraction tests",
                prefix="documents",
                created_by=self.owner,
            )
            write_relationships([RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(self.owner))])
            vendor = vendor_model.objects.create(
                slug="extraction-test-models",
                display_name="Extraction test models",
                website_url="https://example.invalid",
                icon="",
                description="Synthetic tests only",
            )
            provider = provider_model.objects.create(
                owner=self.owner,
                vendor=vendor,
                display_name="Synthetic extraction",
                name="Synthetic extraction",
                backend_class="manual",
                base_url="",
                config={},
                created_by=self.owner,
            )
            self.model = inference_model.objects.create(
                provider=provider,
                name="synthetic-extraction",
                display_name="Synthetic extraction",
                config={},
                created_by=self.owner,
            )
            file_model = apps.get_model("storage", "File")
            self.files = [
                file_model.objects.ingest_bytes(
                    b"FIRST retained source evidence",
                    filename="first.txt",
                    owner_id=self.owner.pk,
                    drive_id=str(self.drive.sqid),
                ),
                file_model.objects.ingest_bytes(
                    b"SECOND retained source evidence",
                    filename="second.txt",
                    owner_id=self.owner.pk,
                    drive_id=str(self.drive.sqid),
                ),
            ]
            write_relationships(
                [RelationshipTuple(to_object_ref(file), "viewer", to_subject_ref(self.owner)) for file in self.files]
            )

    def _extract(self, *, config: dict[str, Any]) -> Any:
        with actor_context(self.owner):
            prepared = prepare_pages(
                files=self.files,
                message_parts=(),
                authorized_target=self.drive,
                config=config,
            )
            return process(
                prepared,
                (),
                schema=SCHEMA,
                model=self.model,
                authorized_target=self.drive,
                engine="fake_document",
                config=config,
            )

    def _retain(
        self,
        *,
        files: Any,
        authorized_target: Any,
        config: dict[str, Any],
        model: Any | None = None,
        recognition_model: Any | None = None,
        message_parts: Any = (),
    ) -> Any:
        prepared = prepare_pages(
            files=files,
            message_parts=message_parts,
            authorized_target=authorized_target,
            config=config,
        )
        return process(
            prepared,
            (),
            schema=SCHEMA,
            model=model,
            recognition_model=recognition_model,
            authorized_target=authorized_target,
            engine="fake_document",
            config=config,
        )

    def _decision(
        self,
        extraction: Any,
        *,
        payload: dict[str, Any] | None = None,
        action: str = "correct_source_facts",
        resolution: dict[str, Any] | None = None,
        verdict: str = "completed",
        resolver: Any | None = None,
        grant_resolver: bool = True,
    ) -> Any:
        resolver = resolver or self.owner
        with system_context(reason="workflows_extraction correction authority"):
            workflow = Workflow.objects.create(name="Extraction correction authority")
            step = Step.objects.create(
                workflow=workflow,
                key="review",
                name="Review",
                step_class="handler",
                config={},
                is_entry=True,
            )
            run = WorkflowRun.objects.create(workflow=workflow, status="succeeded", created_by=self.owner)
            step_run = StepRun.objects.create(
                run=run,
                step=step,
                status="succeeded",
            )
            decision = Decision.objects.create(
                step_run=step_run,
                action=action,
                payload=payload
                or {
                    "extraction_id": str(extraction.sqid),
                    "extraction_revision": extraction.revision,
                },
                target_model=extraction.target._meta.label,
                target_id=str(extraction.target.sqid),
                verdict=verdict,
                resolution=resolution or {"note": "Reviewed source facts"},
                resolved_by=str(to_subject_ref(resolver)) if verdict == "completed" else "",
                created_by=self.owner,
            )
            if grant_resolver:
                write_relationships([RelationshipTuple(to_object_ref(decision), "assignee", to_subject_ref(resolver))])
        return decision

    def _revise(
        self,
        extraction: Any,
        *,
        result: Mapping[str, Any],
        decision: Any,
        identity_mapping: Mapping[str, str] | None = None,
        retired_identities: Mapping[str, str] | None = None,
        confirmed_paths: tuple[str, ...] = (),
    ) -> Any:
        """Exercise the service through one exact admitted native resolution."""

        canonical_decision = type(decision).objects.get(pk=decision.pk)
        resolution = SimpleNamespace(resolved_by=str(canonical_decision.resolved_by))
        target = extraction.target
        admitted_actor = current_actor()
        _binding, expected_parent = type(
            extraction
        ).objects.validate_correction_binding(
            canonical_decision.payload,
            extraction=extraction,
        )
        operation_step_run = SimpleNamespace()
        with patch(
            "angee.workflows_extraction.service.consume_decision_resolution",
            return_value=(canonical_decision, resolution),
        ) as consume:
            if str(canonical_decision.verdict) != "completed":
                consume.side_effect = ValidationError({"decision": "The correction Decision must be completed."})
            with transaction.atomic():
                revised = retain_revision(
                    extraction,
                    decision=decision,
                    result=result,
                    operation_step_run=operation_step_run,
                    resolution_path=("review", "resolutions", 0),
                    input_source="attempt_input",
                    expected_action=str(canonical_decision.action),
                    expected_target=(target._meta.label, str(target.sqid)),
                    identity_mapping=identity_mapping,
                    retired_identities=retired_identities,
                    confirmed_paths=confirmed_paths,
                )
        with system_context(reason="assert correction basis"):
            sources = tuple(
                extraction.sources.select_related("file", "message_part").order_by("position")
            )
            expected_basis = {
                to_object_ref(record)
                for record in dict.fromkeys((
                    extraction,
                    expected_parent,
                    target,
                    *(source.file for source in sources if source.file_id is not None),
                    *(source.message_part for source in sources if source.message_part_id is not None),
                ))
            }
        consume.assert_called_once()
        required_basis = consume.call_args.kwargs["required_record_access"]
        self.assertEqual({to_object_ref(record) for record in required_basis}, expected_basis)
        consume.assert_called_once_with(
            operation_step_run,
            ("review", "resolutions", 0),
            input_source="attempt_input",
            expected_action=str(canonical_decision.action),
            expected_target=(target._meta.label, str(target.sqid)),
            expected_verdict="completed",
            actor=admitted_actor,
            required_record_access=required_basis,
        )
        return revised

    def test_deployment_allowlist_blocks_unapproved_models_and_endpoint_repointing(self) -> None:
        with actor_context(self.owner):
            approved = self.model.deployment_identity()
        policy = {"mapping": [approved], "recognition": []}
        with override_settings(ANGEE_INFERENCE_APPROVED_DEPLOYMENTS=policy):
            evidence = self._extract(config={"page_results": {"0:0": {"number": "LOCAL", "rows": []}}})
        self.assertEqual(evidence.status, "succeeded")

        inference_model = apps.get_model("agents", "InferenceModel")
        with system_context(reason="test unapproved extraction deployment"):
            unapproved = inference_model.objects.create(
                provider=self.model.provider,
                name="unapproved",
                display_name="Unapproved",
                config={"provider_model": "unapproved"},
                created_by=self.owner,
            )
        with actor_context(self.owner), override_settings(ANGEE_INFERENCE_APPROVED_DEPLOYMENTS=policy):
            with self.assertRaisesRegex(DjangoPermissionDenied, "mapping model deployment is not approved"):
                require_approved_model_deployment(unapproved, role="mapping")
            with self.assertRaisesRegex(DjangoPermissionDenied, "recognition model deployment is not approved"):
                require_approved_model_deployment(self.model, role="recognition")

        provider = self.model.provider
        with system_context(reason="test repointed extraction deployment"):
            provider.base_url = "https://external.invalid/v1"
            provider.save(update_fields=("base_url", "updated_at"))
        with override_settings(ANGEE_INFERENCE_APPROVED_DEPLOYMENTS=policy):
            with self.assertRaisesRegex(DjangoPermissionDenied, "mapping model deployment is not approved"):
                require_approved_model_deployment(self.model, role="mapping")

    def test_persists_ordered_evidence_reuses_exact_scope_and_revises_changed_config(self) -> None:
        config = {
            "page_results": {
                "0:0": {"number": "SYN-1", "rows": ["first"]},
                "1:0": {"number": "SYN-1", "rows": ["second"]},
            }
        }
        first = self._extract(config=config)
        reused = self._extract(config=config)
        revised = self._extract(config={**config, "prompt": "changed extraction policy"})

        self.assertEqual(reused.pk, first.pk)
        self.assertEqual((first.revision, revised.revision), (1, 2))
        self.assertEqual(first.result, {"number": "SYN-1", "rows": ["first", "second"]})
        with actor_context(self.owner):
            self.assertEqual(
                list(first.sources.values_list("position", "file_id")),
                [(0, self.files[0].pk), (1, self.files[1].pk)],
            )
            self.assertEqual(
                list(first.pages.values_list("position", "source__position", "source_page")),
                [(0, 0, 0), (1, 1, 0)],
            )

    def test_exact_deterministic_replay_reuses_original_after_corrected_descendant(self) -> None:
        config = {"result": {"number": "SOURCE", "rows": []}, "source_text": "SOURCE"}
        original = self._extract(config=config)
        document = original.document_refs[0]
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = self._revise(
                original,
                result={"number": "SOURCE", "rows": ["reviewed"]},
                decision=decision,
                identity_mapping={document.selector: document.identity, "/rows/0": "new"},
            )

        replayed = self._extract(config=config)

        self.assertEqual(replayed.pk, original.pk)
        self.assertEqual(replayed.result, original.result)
        self.assertEqual(corrected.revision, 2)
        with system_context(reason="assert corrected extraction remains the lineage head"):
            current = (
                type(original)._base_manager.filter(lineage_key=original.lineage_key).order_by("-revision").first()
            )
        self.assertEqual(current.pk, corrected.pk)

        def changed_result(
            sources: Any,
            parts: Any,
            schema: Any,
            *,
            config: Any,
            recognition_used: bool = False,
        ) -> DocumentResult:
            del sources, schema, config, recognition_used
            return DocumentResult(
                {"number": "DIFFERENT", "rows": []},
                tuple(parts),
                {"/number": [{"part_position": 0}]},
                engine_metadata={"route": "fake"},
            )

        with (
            patch("tests.extraction_engines.FakeDocumentEngine.process_parts", side_effect=changed_result),
            self.assertRaisesRegex(ValidationError, "request identity already owns different retained facts"),
        ):
            self._extract(config=config)

        held_config = {**config, "prompt": "retain a correspondence hold"}
        held = self._extract(config=held_config)
        self.assertEqual(held.error_code, "source_hold:identity_correspondence_required")
        advanced = self._extract(
            config={
                **config,
                "result": {"number": "SOURCE", "rows": ["reviewed"]},
                "prompt": "advance after the correspondence hold",
            }
        )

        repeated_hold = self._extract(config=held_config)

        self.assertEqual(repeated_hold.pk, held.pk)
        self.assertEqual(repeated_hold.result, {"number": "SOURCE", "rows": []})
        self.assertEqual(advanced.revision, 4)

    def test_repeated_correspondence_hold_retains_latest_succeeded_identity_authority(self) -> None:
        original = self._extract(
            config={"result": {"number": "SOURCE", "rows": ["source row"]}}
        )
        first_hold = self._extract(config={
            "result": {"number": "REPROCESSED", "rows": ["first", "second"]},
            "prompt": "first structural hold",
        })
        repeated_config = {
            "result": {"number": "REPROCESSED", "rows": ["first", "second"]},
            "prompt": "repeat structural hold without inference",
        }
        with patch(
            "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts"
        ) as provider_call:
            repeated_hold = self._extract(config=repeated_config)

            legacy_provenance = deepcopy(repeated_hold.provenance)
            legacy_provenance["identity_correspondence"].update({
                "expected_base_id": first_hold.pk,
                "last_known_revision": first_hold.revision,
            })
            # Seed the immutable shape released before head CAS and identity
            # authority were separated; production never rewrites this row.
            models.QuerySet(
                model=type(repeated_hold), using=repeated_hold._state.db,
            ).filter(pk=repeated_hold.pk).update(provenance=legacy_provenance)
            repeated_hold.refresh_from_db()
            repaired = self._extract(config=repeated_config)
            exact_retry = self._extract(config=repeated_config)

        self.assertEqual(first_hold.error_code, "source_hold:identity_correspondence_required")
        self.assertEqual(repeated_hold.error_code, "source_hold:identity_correspondence_required")
        self.assertEqual(repaired.error_code, "source_hold:identity_correspondence_required")
        self.assertEqual(
            (original.revision, first_hold.revision, repeated_hold.revision, repaired.revision),
            (1, 2, 3, 4),
        )
        self.assertEqual(exact_retry.pk, repaired.pk)
        self.assertEqual(
            repaired.provenance["identity_correspondence"],
            {
                "expected_base_id": original.pk,
                "continuing_or_new": {},
                "retired": {},
                "last_known_revision": original.revision,
            },
        )
        with actor_context(self.owner):
            authority = type(original).objects.inference_authority_base(
                repaired, actor=self.owner,
            )
        self.assertEqual(authority.pk, original.pk)
        provider_call.assert_not_called()

    def test_reviewed_correction_authority_requires_one_exact_direct_revision(self) -> None:
        original = self._extract(
            config={"result": {"number": "SOURCE", "rows": []}, "source_text": "SOURCE"}
        )
        correspondence_hold = SimpleNamespace(
            status="failed",
            error_code="source_hold:identity_correspondence_required",
            provenance={
                "identity_correspondence": {
                    "last_known_revision": original.revision,
                    "expected_base_id": True,
                }
            },
            revision=original.revision + 1,
            lineage_key=original.lineage_key,
            content_type_id=original.content_type_id,
            object_id=original.object_id,
            schema_id=original.schema_id,
            schema_digest=original.schema_digest,
            document_map=original.document_map,
        )
        document = original.document_refs[0]
        first_decision = self._decision(
            original,
            resolution={"action": "apply_correction", "note": "Add reviewed row"},
        )
        with actor_context(self.owner):
            with self.assertRaisesRegex(
                ValidationError, "differs from the correspondence hold"
            ):
                type(original).objects.inference_authority_base(
                    correspondence_hold, actor=self.owner
                )
            with self.assertRaisesRegex(
                ValidationError, "different extraction revision"
            ):
                type(original).objects.validate_correction_binding(
                    {
                        "extraction_id": str(original.sqid),
                        "extraction_revision": True,
                    },
                    extraction=original,
                )
            reviewed = self._revise(
                original,
                result={"number": "SOURCE", "rows": ["reviewed"]},
                decision=first_decision,
                identity_mapping={document.selector: document.identity, "/rows/0": "new"},
            )
        reviewed_document = reviewed.document_refs[0]
        reviewed_line = reviewed_document.lines[0]
        second_decision = self._decision(
            reviewed,
            resolution={"action": "apply_correction", "note": "Retain another revision"},
        )
        with actor_context(self.owner):
            later = self._revise(
                reviewed,
                result={"number": "REVIEWED", "rows": ["reviewed"]},
                decision=second_decision,
                identity_mapping={
                    reviewed_document.selector: reviewed_document.identity,
                    reviewed_line.selector: reviewed_line.identity,
                },
            )
            resolved_original, authority = type(original).objects.reviewed_correction_authority(
                reviewed,
                actor=self.owner,
                expected_action="correct_source_facts",
                expected_resolution_action="apply_correction",
            )
            resolved_reviewed, later_authority = (
                type(original).objects.reviewed_correction_authority(
                    later,
                    actor=self.owner,
                    expected_action="correct_source_facts",
                    expected_resolution_action="apply_correction",
                )
            )
            with self.assertRaisesRegex(ValidationError, "different authority basis"):
                type(original).objects.reviewed_correction_authority(
                    reviewed,
                    actor=self.owner,
                    expected_action="other_correction",
                    expected_resolution_action="apply_correction",
                )

        self.assertEqual(resolved_original.pk, original.pk)
        self.assertEqual(authority.pk, first_decision.pk)
        self.assertEqual(resolved_reviewed.pk, reviewed.pk)
        self.assertEqual(later_authority.pk, second_decision.pk)

    def test_human_correction_bridges_one_frozen_empty_correspondence_parent(
        self,
    ) -> None:
        schema = {
            "$id": "test.synthetic.document.collection.v1",
            "type": "object",
            "properties": {
                "documents": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "number": {"type": "string"},
                            "rows": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["number", "rows"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["documents"],
            "additionalProperties": False,
        }
        config = {
            "result": {
                "documents": [{"number": "SOURCE", "rows": ["retained row"]}],
            },
            "source_text": "SOURCE retained row",
            "evidence_layout": {
                "document_collection": "/documents",
                "line_collection": "/rows",
            },
        }
        with patch(
            "tests.extraction_engines.FakeDocumentEngine.evidence_layout",
            config["evidence_layout"],
        ), actor_context(self.owner):
            prepared = prepare_pages(
                files=self.files[:1],
                message_parts=(),
                authorized_target=self.drive,
                config=config,
            )
            original = process(
                prepared,
                (),
                schema=schema,
                model=self.model,
                authorized_target=self.drive,
                engine="fake_document",
                config=config,
            )
        manager = type(original).objects
        with actor_context(self.owner):
            hold = manager.create_revision_from_evidence(
                original,
                lineage_key=original.lineage_key,
                reuse_key=hashlib.sha256(b"empty correspondence parent").hexdigest(),
                expected_base_id=original.pk,
                status="failed",
                error_code="source_hold:identity_correspondence_required",
                schema_id=original.schema_id,
                schema=original.schema,
                schema_digest=original.schema_digest,
                engine=str(original.engine),
                model=original.model,
                recognition_model=original.recognition_model,
                engine_config=original.engine_config,
                result={"documents": []},
                provenance={
                    **original.provenance,
                    "claims": {},
                    "unresolved_reasons": ["identity_correspondence_required"],
                },
                content_type_id=original.content_type_id,
                object_id=original.object_id,
                created_by_id=self.owner.pk,
            )
            binding, bound_parent = manager.prepare_correction_binding(
                original,
                actor=self.owner,
            )
        self.assertEqual(bound_parent.pk, hold.pk)
        self.assertTrue(binding.bridges_revision_parent)
        altered_retirement = copy(hold)
        altered_retirement.retired_identities = [
            {
                "identity": original.document_refs[0].identity,
                "kind": "document",
                "reason": "Not the retained authority retirement state",
            }
        ]
        with self.assertRaisesRegex(
            ValidationError,
            "another retained fact authority",
        ):
            manager._correction_revision_parent(original, altered_retirement)
        unbound_decision = self._decision(
            original,
            resolution={"action": "apply_correction", "note": "Stale unbound review"},
        )
        document = original.document_refs[0]
        line = document.lines[0]
        corrected_result = {
            "documents": [{"number": "REVIEWED", "rows": ["retained row"]}],
        }
        identity_mapping = {
            document.selector: document.identity,
            line.selector: line.identity,
        }
        with actor_context(self.owner), self.assertRaisesRegex(
            ValidationError,
            "extraction base is no longer current",
        ):
            self._revise(
                original,
                result=corrected_result,
                decision=unbound_decision,
                identity_mapping=identity_mapping,
            )
        decision = self._decision(
            original,
            payload={
                "extraction_id": str(original.sqid),
                "extraction_revision": original.revision,
                "correction_binding": binding.payload(),
            },
            resolution={"action": "apply_correction", "note": "Restore reviewed facts"},
        )
        spoofed_decision = copy(decision)
        spoofed_decision.payload = {
            "extraction_id": str(original.sqid),
            "extraction_revision": original.revision,
        }
        with actor_context(self.owner), patch.object(
            type(manager),
            "_validated_correction_revision_parent",
            side_effect=PermissionDenied("The temporary Decision grant expired."),
        ) as live_access_check:
            corrected = self._revise(
                original,
                result=corrected_result,
                decision=spoofed_decision,
                identity_mapping=identity_mapping,
            )
            live_access_check.assert_not_called()
        with actor_context(self.owner):
            exact_retry = self._revise(
                original,
                result=corrected_result,
                decision=decision,
                identity_mapping=identity_mapping,
            )
            reviewed_original, reviewed_decision = manager.reviewed_correction_authority(
                corrected,
                actor=self.owner,
                expected_action="correct_source_facts",
                expected_resolution_action="apply_correction",
            )

        self.assertEqual((original.revision, hold.revision, corrected.revision), (1, 2, 3))
        self.assertEqual(exact_retry.pk, corrected.pk)
        self.assertEqual(reviewed_original.pk, original.pk)
        self.assertEqual(reviewed_decision.pk, decision.pk)
        retained = corrected.corrections[-1]
        self.assertEqual(retained.original_extraction_id, str(original.sqid))
        self.assertEqual(retained.original_revision, original.revision)
        self.assertEqual(retained.revision_parent_extraction_id, str(hold.sqid))
        self.assertEqual(retained.revision_parent_revision, hold.revision)
        self.assertEqual(corrected.document_refs, original.document_refs)
        self.assertIn("/documents/0", original.claims)
        self.assertNotIn("/documents/0", corrected.claims)
        with system_context(reason="verify bridged correction evidence clone"):
            self.assertEqual(
                list(corrected.parts.values_list("position", "content_hash", "value")),
                list(original.parts.values_list("position", "content_hash", "value")),
            )

        competing = self._decision(
            original,
            payload={
                "extraction_id": str(original.sqid),
                "extraction_revision": original.revision,
                "correction_binding": binding.payload(),
            },
            resolution={"action": "apply_correction", "note": "Competing correction"},
        )
        with actor_context(self.owner), self.assertRaisesRegex(
            ValidationError,
            "extraction base is no longer current",
        ):
            self._revise(
                original,
                result={
                    "documents": [{"number": "COMPETING", "rows": ["retained row"]}],
                },
                decision=competing,
                identity_mapping=identity_mapping,
            )

        mismatched = self._decision(
            original,
            payload={
                "extraction_id": str(original.sqid),
                "extraction_revision": original.revision,
                "correction_binding": {
                    **binding.payload(),
                    "revision_parent_extraction_id": str(original.sqid),
                    "revision_parent_extraction_revision": original.revision,
                },
            },
            resolution={"action": "apply_correction", "note": "Wrong parent"},
        )
        with actor_context(self.owner), self.assertRaisesRegex(
            ValidationError,
            "extraction base is no longer current",
        ):
            self._revise(
                original,
                result=corrected_result,
                decision=mismatched,
                identity_mapping=identity_mapping,
            )

        next_decision = self._decision(
            corrected,
            resolution={"action": "apply_correction", "note": "Advance current head"},
        )
        with actor_context(self.owner):
            advanced = self._revise(
                corrected,
                result={
                    "documents": [{"number": "ADVANCED", "rows": ["retained row"]}],
                },
                decision=next_decision,
                identity_mapping=identity_mapping,
            )
            historical_retry = self._revise(
                original,
                result=corrected_result,
                decision=decision,
                identity_mapping=identity_mapping,
            )
        self.assertEqual(advanced.revision, 4)
        self.assertEqual(historical_retry.pk, corrected.pk)

    def test_pipeline_successor_bridge_preserves_only_exact_unreviewed_identity(self) -> None:
        original = self._extract(
            config={"result": {"number": "SOURCE", "rows": ["source row"]}}
        )
        successor = self._extract(config={
            "result": {"number": "UPDATED", "rows": ["source row"]},
            "prompt": "same source identities with updated pipeline facts",
        })
        manager = type(original).objects

        with actor_context(self.owner):
            validated = manager.identity_preserving_pipeline_successor(
                original,
                successor,
                actor=self.owner,
            )
        self.assertEqual(validated.pk, successor.pk)

        changed_structure = copy(successor)
        changed_structure.document_map = []
        changed_schema = copy(successor)
        changed_schema.schema_digest = "f" * 64
        retired_identity = copy(successor)
        retired_identity.retired_identities = [
            {"identity": original.document_refs[0].identity, "reason": "Retired"}
        ]
        reviewed_revision = copy(successor)
        reviewed_revision.provenance = {
            **successor.provenance,
            "corrections": [{
                "kind": "human_correction",
                "original_extraction_id": "ext_retained",
                "original_extraction_revision": original.revision,
                "decision_id": "wdc_reviewed",
                "corrected_paths": ["/number"],
            }],
        }
        for invalid in (
            changed_structure,
            changed_schema,
            retired_identity,
            reviewed_revision,
        ):
            with actor_context(self.owner), self.assertRaisesRegex(
                ValidationError,
                "changed retained source identity",
            ):
                manager.identity_preserving_pipeline_successor(
                    original,
                    invalid,
                    actor=self.owner,
                )

    def test_success_held_success_preserves_last_known_document_and_line_identities(self) -> None:
        config = {
            "evidence_layout": {"line_collection": "/rows"},
            "page_results": {
                "0:0": {"number": "SYN-1", "rows": ["first"]},
                "1:0": {"number": "SYN-1", "rows": ["second"]},
            },
        }
        first = self._extract(config=config)
        manager = type(first).objects

        def revision_values(original: Any, *, result: dict[str, Any], status: str, marker: str) -> dict[str, Any]:
            return {
                "lineage_key": original.lineage_key,
                "reuse_key": hashlib.sha256(marker.encode()).hexdigest(),
                "expected_base_id": original.pk,
                "status": status,
                "error_code": "source_hold:incomplete_recognition" if status == "failed" else "",
                "schema_id": original.schema_id,
                "schema": original.schema,
                "schema_digest": original.schema_digest,
                "engine": str(original.engine),
                "model": original.model,
                "recognition_model": original.recognition_model,
                "engine_config": original.engine_config,
                "result": result,
                "provenance": {**original.provenance, "claims": first.claims},
                "content_type_id": original.content_type_id,
                "object_id": original.object_id,
                "created_by_id": self.owner.pk,
            }

        with actor_context(self.owner):
            held = manager.create_revision_from_evidence(
                first, **revision_values(first, result={}, status="failed", marker="held-after-success")
            )
            carried = {
                item.selector: item.identity for document in held.document_refs for item in (document, *document.lines)
            }
            recovered = manager.create_revision_from_evidence(
                held,
                identity_mapping=carried,
                retired_identities={},
                **revision_values(held, result=first.result, status="succeeded", marker="success-after-held"),
            )
            self.assertEqual((first.revision, held.revision, recovered.revision), (1, 2, 3))
            self.assertEqual(first.document_refs, held.document_refs)
            self.assertEqual(first.document_refs, recovered.document_refs)
            self.assertEqual(first.result, recovered.result)
            self.assertEqual(first.claims, recovered.claims)
            self.assertEqual(held.provenance["identity_correspondence"]["last_known_revision"], 1)
            self.assertEqual(first.result, {"number": "SYN-1", "rows": ["first", "second"]})

    def test_automatic_inference_defers_correspondence_until_candidate(self) -> None:
        config = {
            "result": {
                "number": "BASE",
                "rows": ["first line", "second line"],
                "routing_review_reasons": ["missing facts"],
            },
            "inference_mode": "permitted",
            "evidence_layout": {"line_collection": "/rows"},
        }
        schema = {
            **SCHEMA,
            "properties": {
                **SCHEMA["properties"],
                "routing_review_reasons": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        with actor_context(self.owner):
            prepared = prepare_pages(
                files=self.files[:1],
                message_parts=(),
                authorized_target=self.files[0],
                config=config,
            )
            base = process(
                prepared,
                (),
                schema=schema,
                model=self.model,
                authorized_target=self.files[0],
                engine="fake_document",
                config=config,
            )
        document = base.document_refs[0]
        retained_lines = document.lines
        admitted = SimpleNamespace(
            request_key="automatic-correspondence-request",
            input={
                "base_extraction_id": str(base.sqid),
                "base_revision": base.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service.external_operation_request", return_value=admitted),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                return_value=(
                    {
                        "number": "INFERRED",
                        "rows": ["first line", "second line"],
                        "routing_review_reasons": ["missing facts"],
                    },
                    {},
                    {"route": "test"},
                ),
            ),
        ):
            inferred = infer(
                base,
                model=self.model,
                authorized_target=self.files[0],
                operation_step_run=SimpleNamespace(),
            )

        inferred_document = inferred.document_refs[0]
        self.assertEqual(inferred_document.identity, document.identity)
        self.assertEqual(
            tuple(line.identity for line in inferred_document.lines),
            tuple(line.identity for line in retained_lines),
        )
        for changed_lines in (
            ["second line", "first line"],
            ["first line", "second line", "new line"],
        ):
            with self.subTest(changed_lines=changed_lines), self.assertRaisesRegex(
                ValidationError,
                "reviewed correspondence",
            ):
                type(base).objects.automatic_inference_mapping(
                    base,
                    result={"number": "INFERRED", "rows": changed_lines},
                )

    def test_structural_inference_retains_candidate_and_reviewed_mapping_without_reparse(
        self,
    ) -> None:
        config = {
            "result": {
                "number": "BASE",
                "rows": ["first line", "second line"],
                "routing_review_reasons": ["missing facts"],
            },
            "inference_mode": "permitted",
            "evidence_layout": {"line_collection": "/rows"},
        }
        schema = {
            **SCHEMA,
            "properties": {
                **SCHEMA["properties"],
                "routing_review_reasons": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        target = self.files[0]
        with actor_context(self.owner):
            prepared = prepare_pages(
                files=(target,),
                message_parts=(),
                authorized_target=target,
                config=config,
            )
            base = process(
                prepared,
                (),
                schema=schema,
                model=self.model,
                authorized_target=target,
                engine="fake_document",
                config=config,
            )
        candidate = {
            "number": "INFERRED",
            "rows": ["second line", "first line"],
            "routing_review_reasons": ["missing facts"],
        }
        admitted = SimpleNamespace(
            request_key="structural-candidate",
            input={
                "base_extraction_id": str(base.sqid),
                "base_revision": base.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch(
                "angee.workflows_extraction.service.external_operation_request",
                return_value=admitted,
            ),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                return_value=(candidate, {}, {"route": "test"}),
            ),
        ):
            held = infer(
                base,
                model=self.model,
                authorized_target=target,
                operation_step_run=SimpleNamespace(),
            )

        self.assertEqual(held.status, "failed")
        self.assertEqual(
            held.error_code, "source_hold:identity_correspondence_required"
        )
        self.assertEqual(held.result, candidate)
        self.assertEqual(held.document_refs, base.document_refs)
        document = base.document_refs[0]
        reviewed_mapping = {
            document.selector: document.identity,
            "/rows/0": document.lines[1].identity,
            "/rows/1": document.lines[0].identity,
        }
        continuation = SimpleNamespace(
            request_key="reviewed-structural-candidate",
            input={
                "base_extraction_id": str(held.sqid),
                "base_revision": held.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": reviewed_mapping,
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch(
                "angee.workflows_extraction.service.external_operation_request",
                return_value=continuation,
            ),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                side_effect=AssertionError("reviewed correspondence must not reparse"),
            ),
        ):
            resolved = infer(
                held,
                model=self.model,
                authorized_target=target,
                operation_step_run=SimpleNamespace(),
                identity_mapping=reviewed_mapping,
            )

        self.assertEqual(resolved.status, "succeeded")
        self.assertEqual(
            resolved.result,
            {**candidate, "number": "BASE"},
        )
        self.assertEqual(
            tuple(line.identity for line in resolved.document_refs[0].lines),
            (document.lines[1].identity, document.lines[0].identity),
        )

    def test_preliminary_correspondence_hold_infers_before_reviewed_mapping(self) -> None:
        schema = {
            **SCHEMA,
            "properties": {
                **SCHEMA["properties"],
                "routing_review_reasons": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }

        def retained_pair(target: Any, *, marker: str) -> tuple[Any, Any]:
            authority_config = {
                "result": {"number": marker, "rows": ["retained line"]},
                "inference_mode": "permitted",
                "evidence_layout": {"line_collection": "/rows"},
            }
            preliminary_config = {
                "result": {
                    "number": "",
                    "rows": [],
                    "routing_review_reasons": ["missing facts"],
                },
                "inference_mode": "permitted",
                "evidence_layout": {"line_collection": "/rows"},
                "prompt": f"{marker} unresolved deterministic replay",
            }
            with actor_context(self.owner):
                prepared = prepare_pages(
                    files=(target,), message_parts=(), authorized_target=target,
                    config=authority_config,
                )
                authority = process(
                    prepared, (), schema=schema, model=self.model,
                    authorized_target=target, engine="fake_document",
                    config=authority_config,
                )
                prepared = prepare_pages(
                    files=(target,), message_parts=(), authorized_target=target,
                    config=preliminary_config,
                )
                preliminary = process(
                    prepared, (), schema=schema, model=self.model,
                    authorized_target=target, engine="fake_document",
                    config=preliminary_config,
                )
            self.assertEqual(
                preliminary.error_code,
                "source_hold:identity_correspondence_required",
            )
            self.assertEqual(preliminary.document_refs, authority.document_refs)
            self.assertNotIn("inference", preliminary.stage_provenance)
            return authority, preliminary

        authority, preliminary = retained_pair(self.files[0], marker="MATCHED")
        admitted = SimpleNamespace(
            request_key="complete-preliminary-correspondence",
            input={
                "base_extraction_id": str(preliminary.sqid),
                "base_revision": preliminary.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service.external_operation_request", return_value=admitted),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                return_value=(authority.result, {}, {"route": "test"}),
            ) as provider,
        ):
            inferred = infer(
                preliminary, model=self.model, authorized_target=self.files[0],
                operation_step_run=SimpleNamespace(),
            )
            exact_retry = infer(
                preliminary, model=self.model, authorized_target=self.files[0],
                operation_step_run=SimpleNamespace(),
            )

        self.assertEqual(inferred.status, "succeeded")
        self.assertEqual(exact_retry.pk, inferred.pk)
        self.assertEqual(inferred.document_refs, authority.document_refs)
        with actor_context(self.owner):
            self.assertEqual(
                list(inferred.sources.values_list("file_id", "message_part_id")),
                list(preliminary.sources.values_list("file_id", "message_part_id")),
            )
        self.assertEqual(
            inferred.stage_provenance["inference"]["authority_extraction_id"],
            str(authority.sqid),
        )
        provider.assert_called_once()

        authority, preliminary = retained_pair(self.files[1], marker="CHANGED")
        changed_candidate = {
            "number": "CHANGED",
            "rows": ["new first", "new second"],
            "routing_review_reasons": [],
        }
        admitted = SimpleNamespace(
            request_key="populate-preliminary-correspondence",
            input={
                "base_extraction_id": str(preliminary.sqid),
                "base_revision": preliminary.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service.external_operation_request", return_value=admitted),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                return_value=(changed_candidate, {}, {"route": "test"}),
            ) as provider,
        ):
            populated = infer(
                preliminary, model=self.model, authorized_target=self.files[1],
                operation_step_run=SimpleNamespace(),
            )

        self.assertEqual(populated.status, "failed")
        self.assertEqual(
            populated.error_code, "source_hold:identity_correspondence_required",
        )
        self.assertEqual(populated.result, changed_candidate)
        self.assertEqual(populated.document_refs, authority.document_refs)
        self.assertEqual(
            populated.provenance["identity_correspondence"]["expected_base_id"],
            authority.pk,
        )
        self.assertEqual(
            populated.provenance["identity_correspondence"]["last_known_revision"],
            authority.revision,
        )
        provider.assert_called_once()
        populated_admitted = SimpleNamespace(
            request_key="populated-correspondence-requires-review",
            input={
                "base_extraction_id": str(populated.sqid),
                "base_revision": populated.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch(
                "angee.workflows_extraction.service.external_operation_request",
                return_value=populated_admitted,
            ),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts"
            ) as second_provider,
            self.assertRaisesRegex(ValidationError, "explicit reviewed mapping"),
        ):
            infer(
                populated, model=self.model, authorized_target=self.files[1],
                operation_step_run=SimpleNamespace(),
            )
        second_provider.assert_not_called()

    def test_schema_upgrade_hold_retains_prior_fact_identity_authority(self) -> None:
        target = self.files[0]
        base_schema = {
            **SCHEMA,
            "$id": "test.invoice.v1",
            "properties": {
                **SCHEMA["properties"],
                "routing_review_reasons": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        }
        upgraded_schema = {
            **base_schema,
            "$id": "test.invoice.v2",
            "properties": {
                **base_schema["properties"],
                "source_payment_claims": {"type": "array", "items": {"type": "object"}},
            },
        }
        authority_config = {
            "result": {"number": "SOURCE", "rows": ["retained line"]},
            "inference_mode": "permitted",
            "evidence_layout": {"line_collection": "/rows"},
        }
        preliminary_config = {
            "result": {
                "number": "",
                "rows": [],
                "routing_review_reasons": ["missing facts"],
            },
            "inference_mode": "permitted",
            "evidence_layout": {"line_collection": "/rows"},
        }

        def retain(config: dict[str, Any], schema: dict[str, Any]) -> Any:
            prepared = prepare_pages(
                files=(target,), message_parts=(), authorized_target=target,
                config=config,
            )
            return process(
                prepared, (), schema=schema, model=self.model,
                authorized_target=target, engine="fake_document", config=config,
            )

        with actor_context(self.owner):
            authority = retain(authority_config, base_schema)
            preliminary = retain(preliminary_config, base_schema)
            upgraded_hold = retain(preliminary_config, upgraded_schema)
            exact_retry = retain(preliminary_config, upgraded_schema)

        self.assertEqual(
            preliminary.error_code,
            "source_hold:identity_correspondence_required",
        )
        self.assertEqual(preliminary.document_refs, authority.document_refs)
        self.assertEqual(
            upgraded_hold.error_code,
            "source_hold:identity_correspondence_required",
        )
        self.assertEqual(upgraded_hold.document_refs, authority.document_refs)
        self.assertNotEqual(upgraded_hold.schema_digest, authority.schema_digest)
        self.assertEqual(
            upgraded_hold.provenance["identity_correspondence"]["expected_base_id"],
            authority.pk,
        )
        self.assertEqual(
            upgraded_hold.provenance["identity_correspondence"]["last_known_revision"],
            authority.revision,
        )
        self.assertEqual(exact_retry.pk, upgraded_hold.pk)
        with actor_context(self.owner):
            self.assertEqual(
                type(upgraded_hold).objects.inference_authority_base(
                    upgraded_hold, actor=self.owner,
                ).pk,
                authority.pk,
            )

        admitted = SimpleNamespace(
            request_key="infer-upgraded-preliminary-hold",
            input={
                "base_extraction_id": str(upgraded_hold.sqid),
                "base_revision": upgraded_hold.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": {},
                "retired_identities": {},
            },
        )
        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service.external_operation_request", return_value=admitted),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
                return_value=(authority.result, {}, {"route": "test"}),
            ) as provider,
        ):
            inferred = infer(
                upgraded_hold, model=self.model, authorized_target=target,
                operation_step_run=SimpleNamespace(),
            )

        self.assertEqual(inferred.status, "succeeded")
        self.assertEqual(inferred.document_refs, authority.document_refs)
        self.assertEqual(
            inferred.stage_provenance["inference"]["authority_extraction_id"],
            str(authority.sqid),
        )
        provider.assert_called_once()

    def test_correspondence_hold_inference_uses_exact_last_known_fact_authority(self) -> None:
        original = self._extract(
            config={
                "result": {"number": "SOURCE", "rows": ["source row"]},
                "inference_mode": "permitted",
            }
        )
        original_document = original.document_refs[0]
        original_line = original_document.lines[0]
        correction_mapping = {
            original_document.selector: original_document.identity,
            original_line.selector: original_line.identity,
        }
        decision = self._decision(original)
        with actor_context(self.owner):
            authoritative = self._revise(
                original,
                result={"number": "HUMAN", "rows": ["source row"]},
                decision=decision,
                identity_mapping=correction_mapping,
            )
            held_config = {
                "result": {"number": "REPROCESSED", "rows": ["first", "second"]},
                "inference_mode": "permitted",
            }
            held = self._retain(
                files=tuple(reversed(self.files)),
                authorized_target=self.drive,
                config=held_config,
                model=self.model,
            )
        self.assertEqual(held.error_code, "source_hold:identity_correspondence_required")
        self.assertEqual(
            held.parts.with_actor(self.owner).select_related("source").get(position=0).source.file_id,
            authoritative.parts.with_actor(self.owner).select_related("source").get(position=1).source.file_id,
        )

        document = authoritative.document_refs[0]
        line = document.lines[0]
        with self.assertRaisesRegex(ValidationError, "reviewed correspondence"):
            type(authoritative).objects.automatic_inference_mapping(authoritative)
        continuing = {
            document.selector: document.identity,
            line.selector: line.identity,
            "/rows/1": "new",
        }
        admitted = SimpleNamespace(
            request_key="held-authority-request",
            input={
                "base_extraction_id": str(held.sqid),
                "base_revision": held.revision,
                "model_id": str(self.model.sqid),
                "identity_mapping": continuing,
                "retired_identities": {},
            },
        )
        provider_claims = {
            "/number": [{"part_position": 0}],
            "/rows/0": [{"part_position": 0}],
        }
        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service.external_operation_request", return_value=admitted),
            patch(
                "angee.workflows_extraction.engines.InferenceMappingEngine.map_text_parts",
            ) as provider,
        ):
            inferred = infer(
                held,
                model=self.model,
                authorized_target=self.drive,
                operation_step_run=SimpleNamespace(),
                identity_mapping=continuing,
            )
        provider.assert_not_called()
        self.assertEqual(inferred.status, "succeeded")
        self.assertEqual(inferred.result, {"number": "HUMAN", "rows": ["source row", "second"]})
        inferred_document = inferred.document_refs[0]
        self.assertEqual(inferred_document.identity, document.identity)
        self.assertEqual(inferred_document.lines[0].identity, line.identity)
        self.assertNotIn(inferred_document.lines[1].identity, {document.identity, line.identity})
        self.assertEqual(inferred.stage_provenance["inference"]["authority_revision"], 2)
        self.assertEqual(
            inferred.stage_provenance["inference"]["authority_extraction_id"],
            str(authoritative.sqid),
        )
        self.assertEqual(inferred.claims["/rows/0"][0]["part_position"], 1)
        self.assertEqual(
            inferred.parts.with_actor(self.owner).select_related("source").get(position=1).source.file_id,
            authoritative.parts.with_actor(self.owner).select_related("source").get(position=0).source.file_id,
        )

        retired_result, retired_claims, retired_completion = _preserve_retained_authority(
            authoritative,
            {"number": "PROVIDER", "rows": ["new identity row"]},
            provider_claims,
            identity_mapping={document.selector: document.identity, line.selector: "new"},
            retired_identities={line.identity: "Reviewed replacement line"},
            claim_part_positions={0: 0},
        )
        self.assertEqual(retired_result, {"number": "HUMAN", "rows": ["new identity row"]})
        self.assertIn("/rows/0", retired_claims)
        self.assertFalse(retired_completion)

        replaced_result, replaced_claims, replaced_completion = _preserve_retained_authority(
            authoritative,
            {"number": "new identity number", "rows": ["new identity row"]},
            provider_claims,
            identity_mapping={document.selector: "new", line.selector: "new"},
            retired_identities={
                document.identity: "Reviewed replacement document",
                line.identity: "Retired with its document",
            },
            claim_part_positions={0: 0},
        )
        self.assertEqual(
            replaced_result,
            {
                "number": "new identity number",
                "rows": ["new identity row"],
            },
        )
        self.assertEqual(replaced_claims, provider_claims)
        self.assertFalse(replaced_completion)

        structural = SimpleNamespace(
            claims={"/rows": [{"part_position": 0}]},
            corrections=(),
            document_refs=authoritative.document_refs,
            result=authoritative.result,
        )
        with self.assertRaisesRegex(ValidationError, "identity container"):
            _preserve_retained_authority(
                structural,
                {"number": "PROVIDER", "rows": ["provider row"]},
                provider_claims,
                identity_mapping=continuing,
                retired_identities={},
                claim_part_positions={0: 0},
            )

    def test_retains_failed_evidence_and_scopes_raw_values_to_authorized_readers(self) -> None:
        failed = self._extract(config={"failure": True})
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "processing:SyntheticFailure")
        self.assertEqual(failed.result, {})
        failed.with_actor(self.owner)._require_record_access("read")
        with self.assertRaises(PermissionDenied):
            failed.with_actor(self.stranger)._require_record_access("read")
        extraction_model = apps.get_model("workflows_extraction", "Extraction")
        with self.assertRaises(MissingActorError):
            extraction_model.objects.filter(pk=failed.pk).exists()
        with self.assertRaisesRegex(ValueError, "immutable"):
            extraction_model._base_manager.filter(pk=failed.pk).update(status="succeeded")
        with actor_context(self.owner):
            self.assertEqual(failed.sources.count(), 2)
            self.assertEqual(failed.pages.count(), 2)
            self.assertEqual(failed.parts.count(), 2)

        with actor_context(self.stranger):
            self.assertFalse(extraction_model.objects.filter(pk=failed.pk).exists())
        with actor_context(self.owner):
            visible = extraction_model.objects.get(pk=failed.pk)
            self.assertEqual(visible.parts.get(position=0).value, "FIRST retained source evidence")

    def test_document_engine_persists_model_free_raw_parts_and_fingerprints_recognizer(self) -> None:
        config = {
            "result": {"number": "SYN-2", "rows": ["native"]},
            "source_text": "Synthetic invoice attachment",
        }
        with actor_context(self.owner):
            first = self._retain(files=self.files[:1], authorized_target=self.drive, config=config)
            revised = self._retain(
                files=self.files[:1],
                authorized_target=self.drive,
                config=config,
                recognition_model=self.model,
            )
            self.assertEqual(first.result, config["result"])
            self.assertIsNone(first.model_id)
            self.assertEqual(first.stage_provenance["route"], "fake")
            self.assertEqual(
                first.stage_provenance["stages"],
                ["prepare_pages", "collect_carriers", "process_parts"],
            )
            self.assertEqual(first.parts.count(), 1)
            self.assertEqual(first.parts.get().claims["/number"], [{"part_position": 0}])
            self.assertEqual(revised.revision, first.revision + 1)
            self.assertEqual(revised.recognition_model_id, self.model.pk)
            self.assertEqual(revised.provenance["configured_model_roles"], ["recognition"])
            self.assertEqual(revised.provenance["used_model_roles"], [])

    def test_human_correction_clones_parts_retains_unchanged_claims_and_reuses_without_engine(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files,
                authorized_target=self.drive,
                config={
                    "result": {"number": "OLD", "rows": ["same"]},
                    "source_text": "OLD same",
                },
            )
        decision = self._decision(original)
        original_result = dict(original.result)
        original_provenance = dict(original.provenance)

        with (
            actor_context(self.owner),
            patch("angee.workflows_extraction.service._engine_class") as engine_class,
            patch("angee.workflows_extraction.service._document_sources") as acquire_sources,
        ):
            corrected = self._revise(
                original,
                result={"number": "NEW", "rows": ["same"]},
                decision=decision,
            )
            repeated = self._revise(
                original,
                result={"number": "NEW", "rows": ["same"]},
                decision=decision,
            )
        engine_class.assert_not_called()
        acquire_sources.assert_not_called()
        self.assertEqual(original.fact_authority("/number").kind, "source")
        self.assertEqual(original.fact_authority("/rows/0").kind, "source")
        self.assertEqual(repeated.pk, corrected.pk)
        self.assertEqual(corrected.revision, original.revision + 1)
        self.assertEqual(corrected.result, {"number": "NEW", "rows": ["same"]})
        self.assertEqual(corrected.provenance["claims"], {"/rows/0": [{"part_position": 0}]})
        self.assertEqual(corrected.provenance["used_model_roles"], [])
        corrected_number = corrected.fact_authority("/number")
        self.assertEqual(
            (corrected_number.kind, corrected_number.decision_id),
            ("correction", str(decision.sqid)),
        )
        self.assertEqual(corrected.fact_authority("/rows/0").kind, "source")
        self.assertEqual(
            corrected.provenance["corrections"][-1],
            {
                "kind": "human_correction",
                "original_extraction_id": str(original.sqid),
                "original_extraction_revision": original.revision,
                "decision_id": str(decision.sqid),
                "decision_resolved_by": str(to_subject_ref(self.owner)),
                "recorded_by": str(to_subject_ref(self.owner)),
                "corrected_paths": ["/number"],
                "result_digest": corrected.provenance["corrections"][-1]["result_digest"],
            },
        )
        with system_context(reason="verify cloned extraction evidence"):
            self.assertEqual(
                list(original.sources.values_list("position", "file_id", "message_part_id", "content_hash")),
                list(corrected.sources.values_list("position", "file_id", "message_part_id", "content_hash")),
            )
            part_fields = (
                "source__position",
                "position",
                "source_page",
                "mime_type",
                "kind",
                "method",
                "content_hash",
                "width",
                "height",
                "dpi",
                "value",
                "metadata",
                "duration_ms",
            )
            self.assertEqual(
                list(original.parts.values_list(*part_fields)),
                list(corrected.parts.values_list(*part_fields)),
            )
            self.assertEqual(corrected.parts.get(position=0).claims, {"/rows/0": [{"part_position": 0}]})
        original.refresh_from_db()
        self.assertEqual(original.result, original_result)
        self.assertEqual(original.provenance, original_provenance)
        self.assertEqual(Extraction._base_manager.count(), 2)

    def test_human_correction_keeps_admission_actor_as_revision_owner(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files[:1],
                authorized_target=self.drive,
                config={"result": {"number": "OLD", "rows": []}, "source_text": "OLD"},
            )
        decision = self._decision(original, resolver=self.stranger)
        with system_context(reason="grant admission actor decision read"):
            write_relationships([
                RelationshipTuple(to_object_ref(decision), "reader", to_subject_ref(self.owner)),
            ])

        with actor_context(self.owner):
            corrected = self._revise(
                original,
                result={"number": "NEW", "rows": []},
                decision=decision,
            )

        self.assertEqual(corrected.created_by_id, self.owner.pk)
        self.assertTrue(corrected.with_actor(self.owner).has_access("read"))
        self.assertFalse(corrected.with_actor(self.stranger).has_access("read"))
        correction = corrected.provenance["corrections"][-1]
        self.assertEqual(correction["decision_id"], str(decision.sqid))
        self.assertEqual(
            correction["decision_resolved_by"],
            str(to_subject_ref(self.stranger)),
        )
        self.assertEqual(
            correction["recorded_by"],
            str(to_subject_ref(self.stranger)),
        )

    def test_human_correction_clones_ordered_page_evidence(self) -> None:
        original = self._extract(config={"result": {"number": "OLD", "rows": ["row"]}})
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = self._revise(
                original,
                result={"number": "NEW", "rows": ["row"]},
                decision=decision,
            )
        with system_context(reason="verify cloned page evidence"):
            page_fields = (
                "source__position",
                "position",
                "source_page",
                "width",
                "height",
                "dpi",
                "duration_ms",
                "result",
                "engine_metadata",
            )
            self.assertEqual(
                list(original.pages.values_list(*page_fields)),
                list(corrected.pages.values_list(*page_fields)),
            )

    def test_human_correction_classifies_changed_array_element_without_source_authority(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files,
                authorized_target=self.drive,
                config={
                    "result": {"number": "OLD", "rows": ["same"]},
                    "source_text": "OLD same",
                },
            )
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = self._revise(
                original,
                result={"number": "OLD", "rows": ["reviewed"]},
                decision=decision,
            )

        self.assertEqual(original.fact_authority("/rows/0").kind, "source")
        reviewed = corrected.fact_authority("/rows/0")
        self.assertEqual((reviewed.kind, reviewed.decision_id), ("correction", str(decision.sqid)))
        self.assertEqual(corrected.fact_authority("/rows").kind, "unverified")

    def test_human_correction_confirms_same_scalar_through_exact_moved_identity(self) -> None:
        def ungrounded_result(
            sources: Any,
            parts: Any,
            schema: Any,
            *,
            config: Any,
            recognition_used: bool = False,
        ) -> DocumentResult:
            del sources, schema, recognition_used
            return DocumentResult(
                config["result"],
                tuple(parts),
                {},
                engine_metadata={"route": "focused-confirmation-fixture"},
            )

        with (
            patch("tests.extraction_engines.FakeDocumentEngine.process_parts", side_effect=ungrounded_result),
            actor_context(self.owner),
        ):
            original = self._retain(
                files=self.files[:1],
                authorized_target=self.drive,
                config={"result": {"number": "OLD", "rows": ["first", "second"]}},
            )
        document = original.document_refs[0]
        first, second = document.lines
        mapping = {
            document.selector: document.identity,
            "/rows/0": second.identity,
            "/rows/1": first.identity,
        }
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = self._revise(
                original,
                result={"number": "OLD", "rows": ["second", "first"]},
                decision=decision,
                identity_mapping=mapping,
                confirmed_paths=("/rows/0",),
            )
            repeated = self._revise(
                original,
                result={"number": "OLD", "rows": ["second", "first"]},
                decision=decision,
                identity_mapping=mapping,
                confirmed_paths=("/rows/0",),
            )
        self.assertEqual(repeated.pk, corrected.pk)
        self.assertEqual(corrected.corrections[-1].corrected_paths, ("/rows/0",))
        self.assertEqual(
            (corrected.fact_authority("/rows/0").kind, corrected.fact_authority("/rows/0").decision_id),
            ("correction", str(decision.sqid)),
        )
        self.assertEqual(corrected.fact_authority("/rows/1").kind, "unverified")

        invalid_cases = (
            (("/rows",), {"number": "OLD", "rows": ["second", "first"]}, mapping, {}, "not scalar"),
            (("/missing",), {"number": "OLD", "rows": ["second", "first"]}, mapping, {}, "is absent"),
            (("/number",), {"number": "NEW", "rows": ["second", "first"]}, mapping, {}, "changed value"),
            (
                ("/rows/0",),
                {"number": "OLD", "rows": ["new", "first"]},
                {
                    document.selector: document.identity,
                    "/rows/0": "new",
                    "/rows/1": first.identity,
                },
                {second.identity: "Reviewed replacement"},
                "new identity",
            ),
            (("/rows/0", "/rows/0"), {"number": "OLD", "rows": ["second", "first"]}, mapping, {}, "must be unique"),
        )
        for confirmed_paths, result, identity_mapping, retired_identities, message in invalid_cases:
            invalid_decision = self._decision(original)
            with actor_context(self.owner), self.assertRaisesRegex(ValidationError, message):
                self._revise(
                    original,
                    result=result,
                    decision=invalid_decision,
                    identity_mapping=identity_mapping,
                    retired_identities=retired_identities,
                    confirmed_paths=confirmed_paths,
                )

        with (
            actor_context(self.owner),
            self.assertRaisesRegex(ValidationError, "request identity already owns different retained facts"),
        ):
            self._revise(
                original,
                result={"number": "OLD", "rows": ["second", "first"]},
                decision=decision,
                identity_mapping=mapping,
                confirmed_paths=("/rows/1",),
            )

    def test_human_correction_maps_unchanged_claims_by_line_identity_and_marks_replacement(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files,
                authorized_target=self.drive,
                config={
                    "result": {"number": "OLD", "rows": ["first", "second"]},
                    "source_text": "OLD first second",
                },
            )
        document = original.document_refs[0]
        first, second = document.lines
        first_decision = self._decision(original)
        with actor_context(self.owner):
            first_corrected = self._revise(
                original,
                result={"number": "OLD", "rows": ["reviewed", "second"]},
                decision=first_decision,
                identity_mapping={
                    document.selector: document.identity,
                    "/rows/0": first.identity,
                    "/rows/1": second.identity,
                },
            )
        decision = self._decision(first_corrected)
        mapping = {
            document.selector: document.identity,
            "/rows/0": second.identity,
            "/rows/1": first.identity,
        }
        with actor_context(self.owner):
            reordered = self._revise(
                first_corrected,
                result={"number": "OLD", "rows": ["second", "reviewed"]},
                decision=decision,
                identity_mapping=mapping,
            )
        self.assertEqual(
            [(line.identity, line.selector) for line in reordered.document_refs[0].lines],
            [(second.identity, "/rows/0"), (first.identity, "/rows/1")],
        )
        self.assertEqual(
            reordered.claims,
            {
                "/number": [{"part_position": 0}],
                "/rows/0": [{"part_position": 0}],
            },
        )
        self.assertEqual(reordered.fact_authority("/rows/0").kind, "source")
        self.assertEqual(
            reordered.fact_authority("/rows/1").decision_id,
            str(first_decision.sqid),
        )
        self.assertEqual(reordered.corrections[-1].corrected_paths, ())
        with actor_context(self.owner):
            repeated = self._revise(
                first_corrected,
                result={"number": "OLD", "rows": ["second", "reviewed"]},
                decision=decision,
                identity_mapping=mapping,
            )
            self.assertEqual(repeated.pk, reordered.pk)
            with self.assertRaisesRegex(ValidationError, "different retained facts"):
                self._revise(
                    first_corrected,
                    result={"number": "OLD", "rows": ["second", "reviewed"]},
                    decision=decision,
                    identity_mapping={
                        document.selector: document.identity,
                        "/rows/0": first.identity,
                        "/rows/1": second.identity,
                    },
                )

        replacement_decision = self._decision(reordered)
        replacement_mapping = {
            document.selector: document.identity,
            "/rows/0": "new",
            "/rows/1": first.identity,
        }
        with actor_context(self.owner):
            replaced = self._revise(
                reordered,
                result={"number": "OLD", "rows": ["second", "reviewed"]},
                decision=replacement_decision,
                identity_mapping=replacement_mapping,
                retired_identities={second.identity: "Reviewed source-line replacement"},
            )
        self.assertNotEqual(replaced.document_refs[0].lines[0].identity, second.identity)
        self.assertNotIn("/rows/0", replaced.claims)
        replaced_authority = replaced.fact_authority("/rows/0")
        self.assertEqual(
            (replaced_authority.kind, replaced_authority.decision_id),
            ("correction", str(replacement_decision.sqid)),
        )
        self.assertEqual(
            replaced.retired_identities,
            [{"identity": second.identity, "kind": "line", "reason": "Reviewed source-line replacement"}],
        )

        root_replacement_decision = self._decision(replaced)
        current_document = replaced.document_refs[0]
        current_lines = current_document.lines
        with actor_context(self.owner):
            root_replaced = self._revise(
                replaced,
                result={"number": "OLD", "rows": ["second", "reviewed"]},
                decision=root_replacement_decision,
                identity_mapping={"": "new", "/rows/0": "new", "/rows/1": "new"},
                retired_identities={
                    current_document.identity: "Reviewed document replacement",
                    current_lines[0].identity: "Reviewed first-line replacement",
                    current_lines[1].identity: "Reviewed second-line replacement",
                },
            )
        self.assertNotEqual(root_replaced.document_refs[0].identity, current_document.identity)
        self.assertEqual(root_replaced.claims, {})
        self.assertEqual(
            root_replaced.fact_authority("/number").decision_id,
            str(root_replacement_decision.sqid),
        )
        self.assertEqual(
            {item["identity"] for item in root_replaced.retired_identities[-3:]},
            {current_document.identity, *(line.identity for line in current_lines)},
        )

        def ungrounded_result(
            sources: Any,
            parts: Any,
            schema: Any,
            *,
            config: Any,
            recognition_used: bool = False,
        ) -> DocumentResult:
            del sources, schema, config, recognition_used
            return DocumentResult(
                {"number": "OLD", "rows": ["ungrounded"]},
                tuple(parts),
                {"/number": [{"part_position": 0}]},
                engine_metadata={"route": "focused-ungrounded-fixture"},
            )

        with (
            patch(
                "tests.extraction_engines.FakeDocumentEngine.process_parts",
                side_effect=ungrounded_result,
            ),
            actor_context(self.owner),
        ):
            insertion_base = self._retain(
                files=self.files[1:],
                authorized_target=self.files[1],
                config={
                    "result": {"number": "OLD", "rows": ["ungrounded"]},
                },
            )
        insertion_document = insertion_base.document_refs[0]
        insertion_line = insertion_document.lines[0]
        insertion_decision = self._decision(insertion_base)
        with actor_context(self.owner):
            inserted = self._revise(
                insertion_base,
                result={"number": "OLD", "rows": ["reviewed new", "ungrounded"]},
                decision=insertion_decision,
                identity_mapping={
                    "": insertion_document.identity,
                    "/rows/0": "new",
                    "/rows/1": insertion_line.identity,
                },
            )
        self.assertEqual(
            inserted.fact_authority("/rows/0").decision_id,
            str(insertion_decision.sqid),
        )
        self.assertEqual(inserted.fact_authority("/rows/1").kind, "unverified")
        self.assertNotIn("/rows/1", inserted.corrections[-1].corrected_paths)

    def test_human_correction_rejects_invalid_authority_schema_result_and_stale_reuse(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files[:1],
                authorized_target=self.drive,
                config={"result": {"number": "OLD", "rows": []}, "source_text": "OLD"},
            )
        wrong_revision = self._decision(
            original,
            payload={"extraction_id": str(original.sqid), "extraction_revision": original.revision + 1},
        )
        pending = self._decision(original, verdict="pending")
        decision = self._decision(original)
        with actor_context(self.owner):
            with self.assertRaisesRegex(ValidationError, "different extraction revision"):
                self._revise(original, result={"number": "NEW", "rows": []}, decision=wrong_revision)
            with self.assertRaisesRegex(ValidationError, "must be completed"):
                self._revise(original, result={"number": "NEW", "rows": []}, decision=pending)
            with self.assertRaisesRegex(ValidationError, "does not match"):
                self._revise(original, result={"number": 1, "rows": []}, decision=decision)
            corrected = self._revise(original, result={"number": "NEW", "rows": []}, decision=decision)
            with self.assertRaisesRegex(ValidationError, "request identity already owns different retained facts"):
                self._revise(original, result={"number": "OTHER", "rows": []}, decision=decision)
            stale_decision = self._decision(original)
            with self.assertRaisesRegex(ValidationError, "no longer current"):
                self._revise(original, result={"number": "OTHER", "rows": []}, decision=stale_decision)
        self.assertEqual(corrected.revision, original.revision + 1)
        self.assertEqual(Extraction._base_manager.count(), 2)

    def test_human_correction_rejects_current_source_identity(self) -> None:
        with actor_context(self.owner):
            original = self._retain(
                files=self.files[:1],
                authorized_target=self.files[1],
                config={"result": {"number": "OLD", "rows": []}, "source_text": "OLD"},
            )
        decision = self._decision(original)

        file_model = apps.get_model("storage", "File")
        with system_context(reason="workflows_extraction correction source mismatch"):
            file_model._base_manager.filter(pk=self.files[0].pk).update(content_hash="0" * 64)
        with actor_context(self.owner), self.assertRaisesRegex(ValidationError, "file source identity"):
            self._revise(original, result={"number": "NEW", "rows": []}, decision=decision)

    def test_retained_message_part_expansion_preserves_evidence_and_is_idempotent(self) -> None:
        channel = make_integration("retained-part-repair")
        repair_actor = channel.owner
        message_model = apps.get_model("messaging", "Message")
        with (
            system_context(reason="test retained message part"),
            override_settings(
                ANGEE_STORAGE_DEFAULT_DRIVE=self.drive.slug,
            ),
        ):
            [message] = message_model.objects.ingest(
                [
                    ParsedMessage(
                        external_id="retained-part-repair",
                        platform="email",
                        body=ParsedPart(
                            type="multipart/mixed",
                            children=(
                                ParsedPart(type="text/plain", text="Outer retained context"),
                                ParsedPart(
                                    type="message/rfc822",
                                    disposition="attachment",
                                    name="forwarded.eml",
                                    content=b"Subject: Forwarded\r\n\r\nNested body",
                                ),
                            ),
                        ),
                    )
                ],
                channel=channel,
                quote_edges=False,
            )
        with actor_context(repair_actor):
            retained = message.parts.get(type="message/rfc822")
            outer_text = message.parts.get(fragment__text="Outer retained context")
        retained_file_id = retained.file_id
        with system_context(reason="test retained message part target grant"):
            write_relationships(
                [
                    RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(repair_actor)),
                ]
            )
        with actor_context(repair_actor):
            evidence = self._retain(
                files=(retained.file,),
                message_parts=(outer_text,),
                authorized_target=self.drive,
                config={"result": {"number": "OLD", "rows": []}, "source_text": "retained context"},
            )
            source_facts = tuple(
                ExtractionSource._base_manager.filter(extraction=evidence)
                .order_by("position")
                .values_list(
                    "pk",
                    "file_id",
                    "message_part_id",
                    "content_hash",
                )
            )
        with actor_context(self.stranger), self.assertRaises(PermissionDenied):
            message_model.objects.expand_retained_part(
                retained,
                (ParsedPart(type="text/plain", text="Denied"),),
            )
        with actor_context(repair_actor):
            first = message_model.objects.expand_retained_part(
                retained,
                (ParsedPart(type="text/plain", text="Nested body"),),
            )
            repeated = message_model.objects.expand_retained_part(
                retained,
                (ParsedPart(type="text/plain", text="Nested body"),),
            )

        retained.refresh_from_db()
        self.assertEqual(retained.file_id, retained_file_id)
        with system_context(reason="test retained extraction identity"):
            self.assertEqual(
                tuple(
                    ExtractionSource._base_manager.filter(extraction=evidence)
                    .order_by("position")
                    .values_list(
                        "pk",
                        "file_id",
                        "message_part_id",
                        "content_hash",
                    )
                ),
                source_facts,
            )
        self.assertEqual([row.pk for row in repeated], [row.pk for row in first])
        with actor_context(repair_actor):
            self.assertEqual(retained.children.count(), 1)

    def test_message_text_carrier_uses_authorized_file_drive(self) -> None:
        channel = make_integration("file-target-message-carrier")
        message_model = apps.get_model("messaging", "Message")
        with system_context(reason="test File-targeted message carrier"):
            [message] = message_model.objects.ingest(
                [
                    ParsedMessage(
                        external_id="file-target-message-carrier",
                        platform="email",
                        body=ParsedPart(type="text/plain", text="Message-only retained evidence"),
                    )
                ],
                channel=channel,
                quote_edges=False,
            )
            write_relationships(
                [
                    RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(channel.owner)),
                    RelationshipTuple(to_object_ref(self.files[0]), "viewer", to_subject_ref(channel.owner)),
                ]
            )
        with actor_context(channel.owner):
            message_part = message.parts.get(fragment__text="Message-only retained evidence")
            with override_settings(ANGEE_STORAGE_DEFAULT_DRIVE="missing-drive"):
                prepared = prepare_pages(
                    files=(),
                    message_parts=(message_part,),
                    authorized_target=self.files[0],
                )

        [carrier] = prepared.pages[0].carrier_files
        self.assertEqual(carrier.drive_id, self.files[0].drive_id)
        with carrier.open_stream() as stream:
            self.assertEqual(stream.read(), b"Message-only retained evidence")

    def test_profile_programming_error_does_not_persist_invalid_json(self) -> None:
        with actor_context(self.owner), self.assertRaises(ValidationError):
            self._retain(
                files=self.files[:1],
                authorized_target=self.drive,
                config={"nul_result": True},
            )
        self.assertEqual(Extraction._base_manager.count(), 0)

    def test_revision_retry_rolls_back_partial_children_before_recreating_whole_evidence(self) -> None:
        original = models.QuerySet.bulk_create
        failures = 0

        def collide(queryset, objects, *args, **kwargs):
            nonlocal failures
            rows = original(queryset, objects, *args, **kwargs)
            if queryset.model is ExtractionPage and failures == 0:
                failures += 1
                raise IntegrityError("simulated competing revision")
            return rows

        with patch.object(models.QuerySet, "bulk_create", collide):
            evidence = self._extract(config={"result": {"number": "RETRY-1", "rows": []}})
        self.assertEqual(evidence.revision, 1)
        with system_context(reason="inspect extraction retry rollback"):
            self.assertEqual(Extraction._base_manager.count(), 1)
            self.assertEqual(ExtractionSource._base_manager.count(), 2)
            self.assertEqual(ExtractionPage._base_manager.count(), 2)

    def test_unrecoverable_database_failure_leaves_no_partial_evidence(self) -> None:
        original = models.QuerySet.bulk_create

        def refuse(queryset, objects, *args, **kwargs):
            if queryset.model is ExtractionPage:
                raise IntegrityError("persistent constraint failure")
            return original(queryset, objects, *args, **kwargs)

        with patch.object(models.QuerySet, "bulk_create", refuse), self.assertRaises(IntegrityError):
            self._extract(config={"result": {"number": "FAIL-1", "rows": []}})
        with system_context(reason="inspect extraction failure rollback"):
            self.assertEqual(Extraction._base_manager.count(), 0)
            self.assertEqual(ExtractionSource._base_manager.count(), 0)
            self.assertEqual(ExtractionPage._base_manager.count(), 0)

    def test_retained_failure_outcome_is_frozen_by_step_config(self) -> None:
        legacy = ExtractionConfig.model_validate({"schema": {}, "engine": "fake"})
        current = ExtractionConfig.model_validate(
            {
                "schema": {},
                "engine": "fake",
                "retained_failure_outcome": "retained_failure",
            }
        )
        self.assertEqual(legacy.retained_failure_outcome, "failed")
        self.assertEqual(current.retained_failure_outcome, "retained_failure")
