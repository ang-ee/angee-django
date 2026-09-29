"""Extraction resource execution against the isolated host's generated models."""

from __future__ import annotations

import io
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pypdfium2 as pdfium
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.integrate.credentials import CredentialKind
from angee.jobs.enqueue import celery_app
from angee.workflows.maps import MapItem
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows_extraction.contracts import PipelineError, RecognitionResult, Result
from angee.workflows_extraction.inference import derive_text_claims
from angee.workflows_extraction.profiles import ExtractionProfile
from angee.workflows_extraction.providers import (
    NativeExtractionConfig,
    NativeExtractionProvider,
    RecognitionOutput,
)
from angee.workflows_extraction.steps import ProcessEvidenceInput


class RecognitionConfig(NativeExtractionConfig):
    """Select one deterministic recognition failure without changing acquisition."""

    fail_page: int | None = None


class DeterministicRecognition(NativeExtractionProvider):
    """Use real PDF preparation and deterministic recognition without a network."""

    key = "native"
    config_model = RecognitionConfig

    def recognize(self, page, file, model, *, config):
        """Read the protected raster, then return text or the requested failure."""
        page.image(file)
        if page.page_position == config.fail_page:
            raise PipelineError("Recognition unavailable.", stage="recognition", code="unavailable")
        return RecognitionResult(f"Page {page.page_position}")


class DeterministicProfile(ExtractionProfile):
    """Interpret only the text retained by the real recognition step."""

    key = "none"

    def process_parts(self, sources, parts, schema, *, config, recognition_used=False):
        """Preserve successful page text and its claims, including empty input."""
        value = {"text": "\n".join(str(part.value) for part in parts)}
        return Result(
            value,
            tuple(parts),
            derive_text_claims(value, parts),
            ("recognition",) if recognition_used else (),
        )


class ExtractionWorkflowTests(TransactionTestCase):
    """Install the shipped graph and execute its typed map without an adapter."""

    def setUp(self):
        """Build native storage and model fixtures with restored test overrides."""
        self.enterContext(patch.object(celery_app, "send_task"))
        call_command("rebac", "sync", verbosity=0)
        self.enterContext(override_settings(
            ANGEE_EXTRACTION_BACKEND_CLASSES={
                **settings.ANGEE_EXTRACTION_BACKEND_CLASSES,
                "native": f"{__name__}.DeterministicRecognition",
            },
            ANGEE_EXTRACTION_PROFILE_CLASSES={
                **settings.ANGEE_EXTRACTION_PROFILE_CLASSES,
                "none": f"{__name__}.DeterministicProfile",
            },
        ))
        storage = self.enterContext(TemporaryDirectory(prefix="extraction-composed-", dir="/private/tmp"))
        with system_context(reason="composed extraction fixtures"):
            self.actor = get_user_model().objects.create_user(username="extraction-runner")
            grant_role(actor=self.actor, role="angee/role:admin")
            backend = apps.get_model("storage.Backend").objects.create(
                slug="local", label="Local", backend_class="local",
                backend_config={"root": storage, "base_url": "/media/"},
            )
            self.drive = apps.get_model("storage.Drive").objects.create(
                backend=backend, slug="assets", name="Assets", prefix="assets", created_by=self.actor,
            )
            for mime_type, category in (
                ("application/pdf", "document"), ("image/jpeg", "image"), ("text/plain", "document"),
            ):
                apps.get_model("storage.MimeType").objects.get_or_create(
                    mime_type=mime_type, defaults={"category": category, "label": mime_type},
                )
            client = apps.get_model("integrate.OAuthClient").objects.create(
                slug="recognition", display_name="Recognition", client_id="recognition",
            )
            credential = apps.get_model("integrate.Credential").objects.upsert_for_user(
                self.actor, client, CredentialKind.STATIC_TOKEN, {"api_key": "test"},
            )
            vendor = apps.get_model("integrate.Vendor").objects.create(
                slug="recognition", display_name="Recognition",
            )
            provider = apps.get_model("agents.InferenceProvider").objects.create(
                name="Recognition", backend_class="manual", vendor=vendor,
                credential=credential, owner=self.actor, lifecycle="connected",
            )
            self.model = apps.get_model("agents.InferenceModel").objects.create(
                provider=provider, name="deterministic", model_use="multimodal",
            )
        self.workflow = load_workflow(
            "angee.workflows_extraction.document_extraction", actor=self.actor, allow_non_dev=True,
        )
        self.assertIsNotNone(self.workflow.published_id)
        self.assertEqual(set(self.workflow.published.definition.nodes), {
            "prepare_pages", "map_pages", "process_evidence",
        })
        with system_context(reason="composed extraction resource installation assertion"):
            installed = apps.get_model("resources.Resource").objects.get(
                source_addon="angee.workflows_extraction", xref="document_extraction",
            ).target_instance()
            self.assertEqual(installed.pk, self.workflow.pk)

    def execute_document(self, pages, *, fail_page=None):
        """Author only the profile schema and test provider config on the installed graph."""
        draft = deepcopy(self.workflow.draft)
        draft["nodes"]["process_evidence"]["config"]["schema"] = {
            "$id": "urn:test:composed-extraction", "type": "object",
            "properties": {"text": {"type": "string"}}, "required": ["text"],
        }
        draft["nodes"]["map_pages"]["body"]["config"] = {"backend_config": {"fail_page": fail_page}}
        saved = type(self.workflow).objects.save_draft(
            self.workflow, draft=draft, expected_revision=self.workflow.draft_revision, actor=self.actor,
        )
        self.assertEqual(saved.status, "saved")
        self.assertEqual(saved.issues, [])
        type(self.workflow).objects.publish(self.workflow, actor=self.actor)
        content = io.BytesIO()
        if pages:
            with pdfium.PdfDocument.new() as document:
                for _ in range(pages):
                    document.new_page(120, 80).close()
                document.save(content)
        else:
            content.write(b"\xef\xbb\xbf")  # An empty UTF-8 document with a recognizable encoding.
        with actor_context(self.actor):
            source = apps.get_model("storage.File").objects.ingest_bytes(
                content.getvalue(), filename="document.pdf" if pages else "empty.txt", drive_id=str(self.drive.sqid),
            )
        run = start_run(self.workflow, actor=self.actor, input={
            "files": [str(source.sqid)], "message_parts": [],
            "target_model": "storage.File", "target_id": str(source.sqid),
            "model": None, "recognition_model": str(self.model.sqid),
        })
        run_until(run)
        with system_context(reason="composed extraction execution assertions"):
            attempts = apps.get_model("workflows.StepAttempt").objects.filter(step_run__run=run)
            self.assertEqual(run.status, "succeeded", list(attempts.values_list("error", flat=True)))
            prepare = run.step_runs.get(node_key="prepare_pages")
            mapped = run.step_runs.get(node_key="map_pages")
            process = run.step_runs.get(node_key="process_evidence")
            bodies = list(mapped.map_rows().order_by("map_index"))
            self.assertEqual(prepare.attempts.count(), 1)
            self.assertEqual(len(prepare.output["pages"]), pages)
            self.assertEqual([body.map_index for body in bodies], list(range(pages)))
            self.assertEqual([body.attempts.count() for body in bodies], [1] * pages)
            self.assertEqual(mapped.outcome, "failed" if fail_page is not None else "done")
            self.assertEqual((mapped.map_total, mapped.map_settled), (pages, pages))
            self.assertEqual(process.input["recognition"], mapped.output)
            values = ProcessEvidenceInput.model_validate(process.input)
            self.assertTrue(all(isinstance(item, MapItem) for item in values.recognition))
            for item in values.recognition:
                if item.index == fail_page:
                    self.assertEqual(item.outcome, "error")
                    self.assertNotIn("output", mapped.output[item.index])
                else:
                    self.assertEqual(item.outcome, "recognized")
                    self.assertIsInstance(item.output, RecognitionOutput)
            evidence = apps.get_model("workflows_extraction.Extraction").objects.get(
                sqid=run.output["extraction_id"],
            )
            successful = [index for index in range(pages) if index != fail_page]
            self.assertEqual(evidence.result, {"text": "\n".join(f"Page {index}" for index in successful)})
            self.assertEqual(evidence.sources.count(), 1)
            self.assertEqual(evidence.pages.count(), pages or 1)
            self.assertEqual(evidence.parts.count(), len(successful) if pages else 1)
            self.assertEqual(evidence.error_code, "source_hold" if fail_page is not None else "")
            self.assertEqual(evidence.unresolved_reasons, (
                (f"recognition_unavailable:0:{fail_page}",) if fail_page is not None else ()
            ))
            self.assertEqual(run.outcome, "source_hold" if fail_page is not None else "processed")
            self.assertEqual(process.artifacts.count(), 1)

    def test_all_pages_reach_processing_through_the_typed_map(self):
        """Successful page outputs reach the processor in execution index order."""
        self.execute_document(2)

    def test_failed_page_preserves_successful_evidence_and_source_hold(self):
        """The map's failed route retains the remaining recognized pages."""
        self.execute_document(3, fail_page=1)

    def test_empty_document_reaches_processing_with_an_empty_map(self):
        """An empty text source needs no recognition and settles without body rows."""
        self.execute_document(0)
