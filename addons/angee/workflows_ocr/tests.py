"""Composed persistence tests for document extraction evidence."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from PIL import Image, ImageDraw
from rebac import RelationshipTuple, actor_context, system_context, to_object_ref, to_subject_ref, write_relationships

from angee.workflows_ocr.service import extract


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
            write_relationships(
                [RelationshipTuple(to_object_ref(self.drive), "viewer", to_subject_ref(self.owner))]
            )
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
