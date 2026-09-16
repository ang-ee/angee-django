"""Composed persistence tests for document extraction evidence."""

from __future__ import annotations

import hashlib
import io
import tempfile
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
from django.db import IntegrityError, connection, models
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from PIL import Image, ImageDraw
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.messaging.backends import ParsedMessage, ParsedPart
from angee.workflows.attempts import RecoveryMode
from angee.workflows_extraction.engines import DocumentPart, DocumentPipelineError, PageImage, PageResult
from angee.workflows_extraction.managers import _document_mapping, _result_selectors
from angee.workflows_extraction.models import DocumentRef, LineRef
from angee.workflows_extraction.routing import (
    _decode_declared_text,
    _html_text,
    derive_text_claims,
    recognize_pages,
)
from angee.workflows_extraction.service import (
    PreparedDocument,
    PreparedPage,
    _document_sources,
    _merge,
    _unchanged_claims,
    collect_carriers,
    extract,
    json_pointer_value,
    model_deployment_identity,
    reextract,
    revise,
)
from angee.workflows_extraction.steps import OcrExtractConfig, OcrExtractStepImpl
from angee.workflows_extraction_glm.engine import GlmOllamaEngine
from tests.conftest import _clear_model_tables, _create_missing_tables, make_integration
from tests.ocr_engines import FakeOcrEngine
from tests.ocr_models import OCR_MODELS, Extraction, ExtractionPage, ExtractionSource
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS
from tests.test_integrate_vcs import VCS_TEST_MODELS
from tests.test_messaging import MESSAGING_TEST_MODELS
from tests.workflows import Decision, Step, StepRun, Workflow, WorkflowRun


def test_json_pointer_value_resolves_rfc6901_tokens_and_rejects_missing() -> None:
    assert json_pointer_value({"vendor/name": {"tax~id": "CZ123"}}, "/vendor~1name/tax~0id") == "CZ123"
    with pytest.raises(KeyError):
        json_pointer_value({"vendor": {}}, "/vendor/name")


@pytest.fixture()
def ocr_tables(transactional_db):
    """Use the same concrete model graph as messaging, agents, and stored files."""

    models = tuple(dict.fromkeys((*MESSAGING_TEST_MODELS, *VCS_TEST_MODELS, *AGENTS_GRAPHQL_MODELS, *OCR_MODELS)))
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
        source = SimpleNamespace(source_position=0, file=SimpleNamespace(sqid="fil_source"),
                                 message_part=None, content_hash="source")
        native = DocumentPart(0, 0, "text/plain", "native_text", "printed text", "pdf_text", "native")
        raster = PageImage(0, 1, "image/jpeg", b"synthetic-image", 200, 100, 200)
        image_file = SimpleNamespace(sqid="fil_image", content_hash="image", upload_state="ready")
        prepared = PreparedDocument(
            (source,),
            (PreparedPage(0, 0, (native,), ()),
             PreparedPage(0, 1, (), (image_file,), raster, image_file)),
        )
        collected = collect_carriers(prepared, [])
        self.assertEqual(collected.parts, (native,))
        self.assertEqual([(page.source_position, page.page_position) for page in collected.pages],
                         [(0, 0), (0, 1)])
        self.assertEqual([result.value["status"] for result in collected.page_results], ["native", "held"])
        self.assertEqual(collected.hold_reasons, ("recognition_page_0_1_missing",))

    def test_declared_empty_collection_and_reviewed_split_identity(self) -> None:
        layout = {"document_collection": "/items", "line_collection": "/rows"}
        self.assertEqual(_result_selectors({"items": []}, layout), ())
        with self.assertRaisesMessage(ValidationError, "collection is absent"):
            _result_selectors({"other": []}, layout)
        self.assertEqual(
            _result_selectors({"other": "root"}, {**layout, "root_document_on_missing": True}),
            (("", ()),),
        )
        with self.assertRaisesMessage(ValidationError, "explicitly mapped"):
            _document_mapping(
                {"items": [{"label": "reclassified"}]}, layout=layout,
                original=SimpleNamespace(
                    result={"label": "root"}, document_refs=(DocumentRef("root-id", ""),),
                ),
                identity_mapping={}, retired_identities={},
            )

        original = SimpleNamespace(
            result={"items": [
                {"label": "A", "rows": [{"amount": 10}, {"amount": 20}]},
                {"label": "B", "rows": [{"amount": 30}]},
            ]},
            document_refs=(
                DocumentRef("doc-a", "/items/0", (
                    LineRef("line-a1", "/items/0/rows/0"),
                    LineRef("line-a2", "/items/0/rows/1"),
                )),
                DocumentRef("doc-b", "/items/1", (LineRef("line-b", "/items/1/rows/0"),)),
            ),
        )
        rows, retired = _document_mapping(
            {"items": [
                {"label": "B corrected", "rows": [{"amount": 31}]},
                {"label": "A corrected", "rows": [{"amount": 11}]},
                {"label": "C split", "rows": [{"amount": 40}]},
            ]},
            layout=layout, original=original,
            identity_mapping={
                "/items/0": "doc-b", "/items/0/rows/0": "line-b",
                "/items/1": "doc-a", "/items/1/rows/0": "line-a1",
                "/items/2": "new", "/items/2/rows/0": "new",
            },
            retired_identities={"line-a2": "Reviewed line retirement after split"},
        )
        self.assertEqual([row["identity"] for row in rows[:2]], ["doc-b", "doc-a"])
        self.assertNotIn(rows[2]["identity"], {"doc-a", "doc-b", "line-a2"})
        self.assertEqual([row["lines"][0]["identity"] for row in rows[:2]], ["line-b", "line-a1"])
        self.assertEqual(retired, [{
            "identity": "line-a2", "kind": "line",
            "reason": "Reviewed line retirement after split",
        }])

    def test_preserves_required_nullable_value_until_substantive_evidence_replaces_it(self) -> None:
        schema = {
            "type": "object",
            "required": ["invoice_date", "reference"],
            "properties": {
                "invoice_date": {"type": ["string", "null"]},
                "reference": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "optional_note": {"type": ["string", "null"]},
            },
        }

        merged, conflicts = _merge(
            [
                PageResult({"invoice_date": None, "reference": None, "optional_note": None}),
                PageResult({"invoice_date": "2026-09-09", "reference": None}),
            ],
            schema=schema,
        )

        self.assertEqual(merged, {"invoice_date": "2026-09-09", "reference": None})
        self.assertEqual(conflicts, {})

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
            GlmOllamaEngine().map_text_parts((part,), SCHEMA, model=None, config={}, timeout=1)
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

    @override_settings(ANGEE_OCR_MAX_BYTES=10)
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

    @override_settings(ANGEE_OCR_MAX_BYTES=10)
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


def _png(text: str) -> bytes:
    image = Image.new("RGB", (320, 160), "white")
    ImageDraw.Draw(image).text((20, 60), text, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.usefixtures("ocr_tables", "workflow_engine_tables")
class ExtractionServiceTests(TestCase):
    """Exercise the service against the concrete composed runtime models."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.storage_root = tempfile.TemporaryDirectory(prefix="angee-ocr-tests-")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.storage_root.cleanup()
        super().tearDownClass()

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        self.owner = get_user_model().objects.create_user(username="ocr-owner")
        self.stranger = get_user_model().objects.create_user(username="ocr-stranger")
        backend_model = apps.get_model("storage", "Backend")
        drive_model = apps.get_model("storage", "Drive")
        mime_model = apps.get_model("storage", "MimeType")
        vendor_model = apps.get_model("integrate", "Vendor")
        provider_model = apps.get_model("agents", "InferenceProvider")
        inference_model = apps.get_model("agents", "InferenceModel")
        with system_context(reason="workflows_extraction tests setup"):
            mime_model.objects.get_or_create(
                mime_type="image/png",
                defaults={"category": "image", "label": "PNG image", "icon_key": "file-image"},
            )
            backend = backend_model.objects.create(
                slug="ocr-tests",
                label="OCR tests",
                backend_class="local",
                backend_config={"root": self.storage_root.name, "base_url": "/test-media/"},
                created_by=self.owner,
            )
            self.drive = drive_model.objects.create(
                backend=backend,
                slug="ocr-tests",
                name="OCR tests",
                prefix="documents",
                created_by=self.owner,
            )
            write_relationships([RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(self.owner))])
            vendor = vendor_model.objects.create(
                slug="ocr-test-models",
                display_name="OCR test models",
                website_url="https://example.invalid",
                icon="",
                description="Synthetic tests only",
            )
            provider = provider_model.objects.create(
                owner=self.owner,
                vendor=vendor,
                display_name="Synthetic OCR",
                name="Synthetic OCR",
                backend_class="manual",
                base_url="",
                config={},
                created_by=self.owner,
            )
            self.model = inference_model.objects.create(
                provider=provider,
                name="synthetic-ocr",
                display_name="Synthetic OCR",
                config={},
                created_by=self.owner,
            )
            file_model = apps.get_model("storage", "File")
            self.files = [
                file_model.objects.ingest_bytes(
                    _png("FIRST"),
                    filename="first.png",
                    owner_id=self.owner.pk,
                    drive_id=str(self.drive.sqid),
                ),
                file_model.objects.ingest_bytes(
                    _png("SECOND"),
                    filename="second.png",
                    owner_id=self.owner.pk,
                    drive_id=str(self.drive.sqid),
                ),
            ]
            write_relationships(
                [RelationshipTuple(to_object_ref(file), "viewer", to_subject_ref(self.owner)) for file in self.files]
            )

    def _extract(self, *, config: dict[str, Any]) -> Any:
        with actor_context(self.owner):
            return extract(
                files=self.files,
                schema=SCHEMA,
                model=self.model,
                authorized_target=self.drive,
                engine="fake",
                config=config,
            )

    def _decision(
        self,
        extraction: Any,
        *,
        payload: dict[str, Any] | None = None,
        verdict: str = "completed",
    ) -> Any:
        with system_context(reason="workflows_extraction correction authority"):
            workflow = Workflow.objects.create(name="OCR correction authority")
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
                action="correct_source_facts",
                payload=payload or {
                    "extraction_id": str(extraction.sqid),
                    "extraction_revision": extraction.revision,
                },
                verdict=verdict,
                resolution={"note": "Reviewed source facts"},
                resolved_by=str(to_subject_ref(self.owner)) if verdict == "completed" else "",
                created_by=self.owner,
            )
            write_relationships([
                RelationshipTuple(to_object_ref(decision), "assignee", to_subject_ref(self.owner))
            ])
        return decision

    def test_deployment_allowlist_blocks_unapproved_models_and_endpoint_repointing(self) -> None:
        with actor_context(self.owner):
            approved = model_deployment_identity(self.model)
        policy = {"mapping": [approved], "recognition": []}
        with override_settings(ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS=policy):
            evidence = self._extract(config={"page_results": {"0:0": {"number": "LOCAL", "rows": []}}})
        self.assertEqual(evidence.status, "succeeded")

        inference_model = apps.get_model("agents", "InferenceModel")
        with system_context(reason="test unapproved OCR deployment"):
            unapproved = inference_model.objects.create(
                provider=self.model.provider, name="unapproved", display_name="Unapproved",
                config={"provider_model": "unapproved"}, created_by=self.owner,
            )
        with actor_context(self.owner), override_settings(ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS=policy):
            with patch("angee.workflows_extraction.service._document_sources") as acquire_sources:
                with self.assertRaisesRegex(DjangoPermissionDenied, "mapping model deployment is not approved"):
                    extract(
                        files=self.files, schema=SCHEMA, model=unapproved,
                        authorized_target=self.drive, engine="fake", config={},
                    )
                acquire_sources.assert_not_called()

            with patch("angee.workflows_extraction.service._document_sources") as acquire_sources:
                with self.assertRaisesRegex(DjangoPermissionDenied, "recognition model deployment is not approved"):
                    extract(
                        files=self.files, schema=SCHEMA, model=self.model, recognition_model=self.model,
                        authorized_target=self.drive, engine="fake", config={},
                    )
                acquire_sources.assert_not_called()

        provider = self.model.provider
        with system_context(reason="test repointed OCR deployment"):
            provider.base_url = "https://external.invalid/v1"
            provider.save(update_fields=("base_url", "updated_at"))
        with override_settings(ANGEE_OCR_APPROVED_MODEL_DEPLOYMENTS=policy):
            with patch("angee.workflows_extraction.service._document_sources") as acquire_sources:
                with self.assertRaisesRegex(DjangoPermissionDenied, "mapping model deployment is not approved"):
                    self._extract(config={})
                acquire_sources.assert_not_called()

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
                item.selector: item.identity
                for document in held.document_refs for item in (document, *document.lines)
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

    def test_retains_failed_evidence_and_scopes_raw_values_to_authorized_readers(self) -> None:
        failed = self._extract(config={"result": {"unvalidated_raw": "private synthetic value"}})
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "result_validation:ValidationError")
        self.assertEqual(failed.result, {"unvalidated_raw": "private synthetic value"})
        with actor_context(self.owner):
            self.assertEqual(failed.sources.count(), 2)
            self.assertEqual(failed.pages.count(), 2)

        extraction_model = apps.get_model("workflows_extraction", "Extraction")
        with actor_context(self.stranger):
            self.assertFalse(extraction_model.objects.filter(pk=failed.pk).exists())
        with actor_context(self.owner):
            visible = extraction_model.objects.get(pk=failed.pk)
            self.assertEqual(visible.result["unvalidated_raw"], "private synthetic value")

    def test_document_engine_persists_model_free_raw_parts_and_fingerprints_recognizer(self) -> None:
        config = {
            "result": {"number": "SYN-2", "rows": ["native"]},
            "source_text": "Synthetic invoice attachment",
        }
        with actor_context(self.owner):
            first = extract(
                files=self.files[:1],
                schema=SCHEMA,
                model=None,
                authorized_target=self.drive,
                engine="fake_document",
                config=config,
            )
            revised = extract(
                files=self.files[:1],
                schema=SCHEMA,
                model=None,
                recognition_model=self.model,
                authorized_target=self.drive,
                engine="fake_document",
                config=config,
            )
            self.assertEqual(first.result, config["result"])
            self.assertIsNone(first.model_id)
            self.assertEqual(first.provenance["document"], {"route": "fake", "pipeline_duration_ms": 0})
            self.assertEqual(first.parts.count(), 1)
            self.assertEqual(first.parts.get().claims["/number"], [{"part_position": 0}])
            self.assertEqual(revised.revision, first.revision + 1)
            self.assertEqual(revised.recognition_model_id, self.model.pk)
            self.assertEqual(revised.provenance["configured_model_roles"], ["recognition"])
            self.assertEqual(revised.provenance["used_model_roles"], [])

    def test_human_correction_clones_parts_retains_unchanged_claims_and_reuses_without_engine(self) -> None:
        with actor_context(self.owner):
            original = extract(
                files=self.files,
                schema=SCHEMA,
                model=None,
                authorized_target=self.drive,
                engine="fake_document",
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
            corrected = revise(
                original,
                result={"number": "NEW", "rows": ["same"]},
                decision=decision,
            )
            repeated = revise(
                original,
                result={"number": "NEW", "rows": ["same"]},
                decision=decision,
            )
        engine_class.assert_not_called()
        acquire_sources.assert_not_called()
        self.assertEqual(original.fact_authority("/number").kind, "source")
        self.assertEqual(original.fact_authority("/rows").kind, "source")
        self.assertEqual(repeated.pk, corrected.pk)
        self.assertEqual(corrected.revision, original.revision + 1)
        self.assertEqual(corrected.result, {"number": "NEW", "rows": ["same"]})
        self.assertEqual(corrected.provenance["claims"], {"/rows": [{"part_position": 0}]})
        self.assertEqual(corrected.provenance["used_model_roles"], [])
        corrected_number = corrected.fact_authority("/number")
        self.assertEqual(
            (corrected_number.kind, corrected_number.decision_id),
            ("correction", str(decision.sqid)),
        )
        self.assertEqual(corrected.fact_authority("/rows").kind, "source")
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
                "source__position", "position", "source_page", "mime_type", "kind", "method",
                "content_hash", "width", "height", "dpi", "value", "metadata", "duration_ms",
            )
            self.assertEqual(
                list(original.parts.values_list(*part_fields)),
                list(corrected.parts.values_list(*part_fields)),
            )
            self.assertEqual(corrected.parts.get(position=0).claims, {"/rows": [{"part_position": 0}]})
        original.refresh_from_db()
        self.assertEqual(original.result, original_result)
        self.assertEqual(original.provenance, original_provenance)
        self.assertEqual(Extraction._base_manager.count(), 2)

    def test_human_correction_clones_ordered_page_evidence(self) -> None:
        original = self._extract(config={"result": {"number": "OLD", "rows": ["row"]}})
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = revise(
                original,
                result={"number": "NEW", "rows": ["row"]},
                decision=decision,
            )
        with system_context(reason="verify cloned page evidence"):
            page_fields = (
                "source__position", "position", "source_page", "width", "height", "dpi",
                "duration_ms", "result", "engine_metadata",
            )
            self.assertEqual(
                list(original.pages.values_list(*page_fields)),
                list(corrected.pages.values_list(*page_fields)),
            )

    def test_human_correction_classifies_changed_array_element_without_source_authority(self) -> None:
        with actor_context(self.owner):
            original = extract(
                files=self.files,
                schema=SCHEMA,
                model=None,
                authorized_target=self.drive,
                engine="fake_document",
                config={
                    "result": {"number": "OLD", "rows": ["same"]},
                    "source_text": "OLD same",
                },
            )
        decision = self._decision(original)
        with actor_context(self.owner):
            corrected = revise(
                original,
                result={"number": "OLD", "rows": ["reviewed"]},
                decision=decision,
            )

        self.assertEqual(original.fact_authority("/rows").kind, "source")
        reviewed = corrected.fact_authority("/rows/0")
        self.assertEqual((reviewed.kind, reviewed.decision_id), ("correction", str(decision.sqid)))
        self.assertEqual(corrected.fact_authority("/rows").kind, "unverified")

    def test_human_correction_rejects_invalid_authority_schema_result_and_stale_reuse(self) -> None:
        with actor_context(self.owner):
            original = extract(
                files=self.files[:1],
                schema=SCHEMA,
                model=None,
                authorized_target=self.drive,
                engine="fake_document",
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
                revise(original, result={"number": "NEW", "rows": []}, decision=wrong_revision)
            with self.assertRaisesRegex(ValidationError, "must be completed"):
                revise(original, result={"number": "NEW", "rows": []}, decision=pending)
            with self.assertRaisesRegex(ValidationError, "does not match"):
                revise(original, result={"number": 1, "rows": []}, decision=decision)
            corrected = revise(original, result={"number": "NEW", "rows": []}, decision=decision)
            with self.assertRaisesRegex(ValidationError, "different correction revision"):
                revise(original, result={"number": "OTHER", "rows": []}, decision=decision)
            stale_decision = self._decision(original)
            with self.assertRaisesRegex(ValidationError, "no longer the current"):
                revise(original, result={"number": "OTHER", "rows": []}, decision=stale_decision)
        self.assertEqual(corrected.revision, original.revision + 1)
        self.assertEqual(Extraction._base_manager.count(), 2)

    def test_human_correction_requires_all_reads_and_current_source_identity(self) -> None:
        with actor_context(self.owner):
            original = extract(
                files=self.files[:1],
                schema=SCHEMA,
                model=None,
                authorized_target=self.files[1],
                engine="fake_document",
                config={"result": {"number": "OLD", "rows": []}, "source_text": "OLD"},
            )
        decision = self._decision(original)
        with actor_context(self.stranger), self.assertRaisesRegex(
            DjangoPermissionDenied, "extraction is required"
        ):
            revise(original, result={"number": "NEW", "rows": []}, decision=decision)
        with system_context(reason="grant correction extraction read"):
            write_relationships([
                RelationshipTuple(to_object_ref(original), "viewer", to_subject_ref(self.stranger)),
            ])
        with actor_context(self.stranger), self.assertRaisesRegex(DjangoPermissionDenied, "target"):
            revise(original, result={"number": "NEW", "rows": []}, decision=decision)
        with system_context(reason="grant correction target read"):
            write_relationships([
                RelationshipTuple(to_object_ref(self.files[1]), "viewer", to_subject_ref(self.stranger)),
            ])
        with actor_context(self.stranger), self.assertRaisesRegex(DjangoPermissionDenied, "source"):
            revise(original, result={"number": "NEW", "rows": []}, decision=decision)
        with system_context(reason="grant correction source read"):
            write_relationships([
                RelationshipTuple(to_object_ref(self.files[0]), "viewer", to_subject_ref(self.stranger)),
            ])
        with actor_context(self.stranger), self.assertRaisesRegex(DjangoPermissionDenied, "Decision"):
            revise(original, result={"number": "NEW", "rows": []}, decision=decision)

        file_model = apps.get_model("storage", "File")
        with system_context(reason="workflows_extraction correction source mismatch"):
            file_model._base_manager.filter(pk=self.files[0].pk).update(content_hash="0" * 64)
        with actor_context(self.owner), self.assertRaisesRegex(ValidationError, "file source identity"):
            revise(original, result={"number": "NEW", "rows": []}, decision=decision)

    def test_retained_message_part_expansion_preserves_evidence_and_is_idempotent(self) -> None:
        channel = make_integration("retained-part-repair")
        repair_actor = channel.owner
        message_model = apps.get_model("messaging", "Message")
        with system_context(reason="test retained message part"), override_settings(
            ANGEE_STORAGE_DEFAULT_DRIVE=self.drive.slug,
        ):
            [message] = message_model.objects.ingest(
                [ParsedMessage(
                    external_id="retained-part-repair",
                    platform="email",
                    body=ParsedPart(
                        type="multipart/mixed",
                        children=(
                            ParsedPart(type="text/plain", text="Outer retained context"),
                            ParsedPart(
                                type="message/rfc822", disposition="attachment",
                                name="forwarded.eml", content=b"Subject: Forwarded\r\n\r\nNested body",
                            ),
                        ),
                    ),
                )],
                channel=channel,
                quote_edges=False,
            )
        with actor_context(repair_actor):
            retained = message.parts.get(type="message/rfc822")
            outer_text = message.parts.get(fragment__text="Outer retained context")
        retained_file_id = retained.file_id
        with system_context(reason="test retained message part target grant"):
            write_relationships([
                RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(repair_actor)),
            ])
        with actor_context(repair_actor):
            evidence = extract(
                files=(retained.file,), message_parts=(outer_text,), schema=SCHEMA, model=None,
                authorized_target=self.drive, engine="fake_document",
                config={"result": {"number": "OLD", "rows": []}, "source_text": "retained context"},
            )
            source_facts = tuple(
                ExtractionSource._base_manager.filter(extraction=evidence).order_by("position").values_list(
                    "pk", "file_id", "message_part_id", "content_hash",
                )
            )
        with actor_context(self.stranger), self.assertRaises(PermissionDenied):
            message_model.objects.expand_retained_part(
                retained, (ParsedPart(type="text/plain", text="Denied"),),
            )
        with actor_context(repair_actor):
            first = message_model.objects.expand_retained_part(
                retained, (ParsedPart(type="text/plain", text="Nested body"),),
            )
            repeated = message_model.objects.expand_retained_part(
                retained, (ParsedPart(type="text/plain", text="Nested body"),),
            )

        retained.refresh_from_db()
        self.assertEqual(retained.file_id, retained_file_id)
        with system_context(reason="test retained extraction identity"):
            self.assertEqual(
                tuple(ExtractionSource._base_manager.filter(extraction=evidence).order_by("position").values_list(
                    "pk", "file_id", "message_part_id", "content_hash",
                )),
                source_facts,
            )
        self.assertEqual([row.pk for row in repeated], [row.pk for row in first])
        with actor_context(repair_actor):
            self.assertEqual(retained.children.count(), 1)

    def test_document_engine_retains_validation_failure_before_postgres_json_null_error(self) -> None:
        with actor_context(self.owner):
            failed = extract(
                files=self.files[:1],
                schema=SCHEMA,
                model=None,
                authorized_target=self.drive,
                engine="fake_document",
                config={"nul_result": True},
            )
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.error_code, "result_validation:ValidationError")
            self.assertEqual(failed.result, {})
            self.assertEqual(failed.sources.count(), 1)
            self.assertEqual(failed.parts.count(), 0)

    def test_engine_io_finishes_before_atomic_persistence_and_exact_reuse_skips_engine(self) -> None:
        def page(engine, image, schema, **kwargs):
            self.assertFalse(connection.in_atomic_block)
            return PageResult({"number": "IO-1", "rows": ["row"]})

        with patch.object(FakeOcrEngine, "extract_page", autospec=True, side_effect=page) as recognize:
            first = self._extract(config={})
            reused = self._extract(config={})
        self.assertEqual(first.pk, reused.pk)
        self.assertEqual(recognize.call_count, len(self.files))

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
        self.assertEqual(Extraction._base_manager.count(), 0)
        self.assertEqual(ExtractionSource._base_manager.count(), 0)
        self.assertEqual(ExtractionPage._base_manager.count(), 0)

    def test_recovery_retains_failed_revision_and_journals_only_the_new_reference(self) -> None:
        failed = self._extract(config={"result": {"private": "unvalidated"}})
        run = SimpleNamespace(
            run=SimpleNamespace(created_by=self.owner),
            step=SimpleNamespace(config={"schema": {"type": "object"}, "engine": "fake"}),
        )
        source_attempt = SimpleNamespace(output={"extraction_id": str(failed.sqid), "revision": 1})
        with patch.object(FakeOcrEngine, "extract_page", return_value=PageResult({"number": "RECOVERED", "rows": []})):
            outcome = OcrExtractStepImpl().run_recovery(
                run,
                now=timezone.now(),
                source_attempt=source_attempt,
                mode=RecoveryMode.FRESH,
            )
            with actor_context(self.owner):
                repeated = reextract(failed)
        self.assertEqual(outcome.outcome, "extracted")
        self.assertEqual(outcome.output, {"extraction_id": str(repeated.sqid), "revision": 2})
        self.assertEqual(Extraction._base_manager.count(), 2)
        failed.refresh_from_db()
        self.assertEqual(failed.result, {"private": "unvalidated"})
        self.assertEqual(failed.status, "failed")
        with self.assertRaises(ValidationError), actor_context(self.owner):
            reextract(repeated)

    def test_retained_failure_outcome_is_frozen_by_step_config(self) -> None:
        legacy = OcrExtractConfig.model_validate({"schema": {}, "engine": "fake"})
        current = OcrExtractConfig.model_validate({
            "schema": {}, "engine": "fake", "retained_failure_outcome": "retained_failure",
        })
        self.assertEqual(legacy.retained_failure_outcome, "failed")
        self.assertEqual(current.retained_failure_outcome, "retained_failure")
