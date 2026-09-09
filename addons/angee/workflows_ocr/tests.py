"""Composed persistence tests for document extraction evidence."""

from __future__ import annotations

import hashlib
import io
import tempfile
from types import SimpleNamespace
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image, ImageDraw
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.workflows_ocr.engines import DocumentPart, DocumentPipelineError, PageImage, PageResult
from angee.workflows_ocr.routing import (
    _decode_declared_text,
    _html_text,
    derive_text_claims,
    map_text_parts,
    recognize_pages,
)
from angee.workflows_ocr.service import _document_sources, _merge, extract

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

    def test_missing_models_retain_acquired_evidence(self) -> None:
        part = DocumentPart(0, 0, "text/plain", "native_text", "Invoice 22121", "test", "a" * 64)
        with self.assertRaises(DocumentPipelineError) as mapping_error:
            map_text_parts((part,), SCHEMA, model=None, config={}, timeout=1)
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
        with system_context(reason="workflows_ocr tests setup"):
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

    def test_retains_failed_evidence_and_scopes_raw_values_to_authorized_readers(self) -> None:
        failed = self._extract(config={"result": {"unvalidated_raw": "private synthetic value"}})
        self.assertEqual(failed.status, "failed")
        self.assertEqual(failed.error_code, "ValidationError")
        self.assertEqual(failed.result, {"unvalidated_raw": "private synthetic value"})
        with actor_context(self.owner):
            self.assertEqual(failed.sources.count(), 2)
            self.assertEqual(failed.pages.count(), 2)

        extraction_model = apps.get_model("workflows_ocr", "Extraction")
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
            self.assertEqual(first.provenance["document"], {"route": "fake"})
            self.assertEqual(first.parts.count(), 1)
            self.assertEqual(first.parts.get().claims["/number"], [{"part_position": 0}])
            self.assertEqual(revised.revision, first.revision + 1)
            self.assertEqual(revised.recognition_model_id, self.model.pk)
            self.assertEqual(revised.provenance["configured_model_roles"], ["recognition"])
            self.assertEqual(revised.provenance["used_model_roles"], [])

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
            self.assertEqual(failed.error_code, "ValidationError")
            self.assertEqual(failed.result, {})
            self.assertEqual(failed.sources.count(), 1)
            self.assertEqual(failed.parts.count(), 0)
