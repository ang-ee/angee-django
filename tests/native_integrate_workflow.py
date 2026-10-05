"""Archive graph execution against a generated host and real review/map owners."""

from __future__ import annotations

from tempfile import TemporaryDirectory
from types import MethodType, SimpleNamespace
from unittest.mock import Mock, patch

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context
from rebac.roles import grant as grant_role

from angee.base.fields import SqidField
from angee.jobs.enqueue import celery_app
from angee.workflows.context import StepContext
from angee.workflows.testing.drivers import decide, load_workflow, run_until, start_run
from angee.workflows_integrate.archive_steps import ArchiveExecute, ArchiveExtractor, ArchiveMappingUnit


class TestFileExtractor(ArchiveExtractor):
    """A deterministic file importer with a heartbeat and target journal."""

    key = "test_file"
    label = "File archive"
    target_resource = "storage.Drive"

    def recognizes(self, subject):
        return subject.filename == "bundle.zip"

    def execute(self, subject, target_pk, reporter):
        reporter.heartbeat()
        return {"source": str(subject.sqid), "target": target_pk}


class TestDriveExtractor(ArchiveExtractor):
    """A deterministic mounted-tree importer using the same target contract."""

    key = "test_drive"
    label = "Mounted archive"
    subject_resource = "storage.Drive"
    target_resource = "storage.Drive"

    def recognizes(self, subject):
        return subject.slug == "source"

    def execute(self, subject, target_pk, reporter):
        reporter.heartbeat()
        return {"source": str(subject.sqid), "target": target_pk}


class TestFailingFileExtractor(TestFileExtractor):
    """Fail one mapped item while the other item's evidence remains."""

    key = "test_fail"
    label = "Unavailable import"

    def execute(self, subject, target_pk, reporter):
        reporter.heartbeat()
        raise RuntimeError("Import unavailable.")


class TestOtherTargetExtractor(TestFileExtractor):
    """Recognize the same source for an incompatible target model."""

    key = "test_other_target"
    label = "Different target"
    target_resource = "storage.File"


class ArchiveWorkflowTests(TransactionTestCase):
    """Install both resource rows, review mappings and execute their map bodies."""

    def setUp(self):
        self.enterContext(patch.object(celery_app, "send_task"))
        call_command("rebac", "sync", verbosity=0)
        self.enterContext(patch.dict(settings.ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES, {
            "test_file": f"{__name__}.TestFileExtractor",
            "test_drive": f"{__name__}.TestDriveExtractor",
        }))
        root = self.enterContext(TemporaryDirectory(prefix="archive-workflow-"))
        with system_context(reason="archive graph fixtures"):
            self.actor = get_user_model().objects.create_user(username="archive-runner")
            grant_role(actor=self.actor, role="angee/role:admin")
            backend = apps.get_model("storage.Backend").objects.create(
                slug="local", label="Local", backend_class="local",
                backend_config={"root": root, "base_url": "/media/"},
            )
            drive = apps.get_model("storage.Drive")
            self.source = drive.objects.create(
                backend=backend, slug="source", name="Source", prefix="source", created_by=self.actor,
            )
            self.target = drive.objects.create(
                backend=backend, slug="target", name="Target", prefix="target", created_by=self.actor,
            )
            apps.get_model("storage.MimeType").objects.get_or_create(
                mime_type="application/zip", defaults={"category": "archive", "label": "ZIP archive"},
            )

    def test_execute_requires_target_write_before_effect_and_uses_authorized_id(self):
        """A read-only reviewer cannot import; the extractor receives the resolved target id."""
        with system_context(reason="archive target reader"):
            reader = get_user_model().objects.create_user(username="archive-reader")
        self.target.with_actor(self.actor).grant_record_access("viewer", reader)
        canonical = str(self.target.sqid)
        padded = SqidField(prefix=self.target.sqid_prefix, min_length=24).public_id_from_value(self.target.pk)
        self.assertNotEqual(padded, canonical)
        context = SimpleNamespace(
            subject=self.source, input=ArchiveMappingUnit(extractor="test_drive", target=padded), actor=reader,
            heartbeat=Mock(), begin_effect=Mock(), record=Mock(), done=Mock(side_effect=lambda value, **_: value),
        )
        context.load = MethodType(StepContext.load, context)
        self.assertTrue(self.target.with_actor(reader).has_access("read"))
        self.assertFalse(self.target.with_actor(reader).has_access("write"))
        with patch.object(TestDriveExtractor, "execute") as execute:
            execute.return_value = {"target": canonical}
            with self.assertRaises(PermissionDenied):
                ArchiveExecute().run(context)
            context.heartbeat.assert_not_called()
            context.begin_effect.assert_not_called()
            execute.assert_not_called()

            context.actor = self.actor
            result = ArchiveExecute().run(context)
            self.assertEqual(result.target, canonical)
            self.assertEqual(result.result, {"target": canonical})
            self.assertEqual(execute.call_args.args[1], canonical)
            context.begin_effect.assert_called_once()

    def test_file_and_drive_resources_review_then_map(self):
        with actor_context(self.actor):
            file = apps.get_model("storage.File").objects.ingest_bytes(
                b"fixture", filename="bundle.zip", drive_id=str(self.source.sqid),
            )
        for suffix, subject in (("file", file), ("drive", self.source)):
            with self.subTest(subject=suffix):
                workflow = load_workflow(
                    f"angee.workflows_integrate.archive_import_{suffix}",
                    actor=self.actor, allow_non_dev=True,
                )
                self.assertIsNotNone(workflow.published_id)
                self.assertEqual(set(workflow.published.definition.nodes), {"probe", "gate", "import_units", "summary"})
                run = start_run(workflow, actor=self.actor, subject=subject)
                run_until(run)
                with system_context(reason="archive review assertion"):
                    gate = run.step_runs.get(node_key="gate")
                    self.assertEqual(gate.waiting_kind, "decision")
                    decision = apps.get_model("decisions.Decision").objects.get(pk=gate.decision_id)
                    self.assertEqual(gate.state["mappings"][str(self.target.sqid)][0]["extractor"], f"test_{suffix}")
                decide(decision, actor=self.actor, chosen=[str(self.target.sqid)])
                run_until(run)
                with system_context(reason="archive result assertion"):
                    run.refresh_from_db()
                    self.assertEqual(run.status, "succeeded")
                    self.assertEqual(run.outcome, "complete")
                    mapped = run.step_runs.get(node_key="import_units")
                    self.assertEqual(mapped.map_total, 1)
                    self.assertEqual(mapped.output[0]["outcome"], "completed")
                    self.assertEqual(mapped.output[0]["output"]["result"], {
                        "source": str(subject.sqid), "target": str(self.target.sqid),
                    })
                    self.assertEqual(mapped.map_rows().get().records.count(), 1)

    def test_unrecognized_source_finishes_without_review(self):
        with actor_context(self.actor):
            file = apps.get_model("storage.File").objects.ingest_bytes(
                b"fixture", filename="unknown.zip", drive_id=str(self.source.sqid),
            )
        workflow = load_workflow(
            "angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True,
        )
        run = start_run(workflow, actor=self.actor, subject=file)
        run_until(run)
        with system_context(reason="unrecognized archive assertion"):
            run.refresh_from_db()
            self.assertEqual((run.status, run.outcome), ("succeeded", "unrecognized"))
            self.assertEqual(run.step_runs.get(node_key="gate").status, "skipped")

    def test_reviewer_can_skip_without_importing(self):
        with actor_context(self.actor):
            file = apps.get_model("storage.File").objects.ingest_bytes(
                b"fixture", filename="bundle.zip", drive_id=str(self.source.sqid),
            )
        workflow = load_workflow(
            "angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True,
        )
        run = start_run(workflow, actor=self.actor, subject=file)
        run_until(run)
        with system_context(reason="skip archive review"):
            gate = run.step_runs.get(node_key="gate")
            decision = apps.get_model("decisions.Decision").objects.get(pk=gate.decision_id)
        decide(decision, actor=self.actor, chosen=["skip"])
        run_until(run)
        with system_context(reason="skip archive result"):
            run.refresh_from_db()
            self.assertEqual((run.status, run.outcome), ("succeeded", "skipped"))
            self.assertEqual(run.step_runs.get(node_key="import_units").status, "skipped")

    def test_failed_map_item_preserves_successful_item(self):
        with actor_context(self.actor):
            file = apps.get_model("storage.File").objects.ingest_bytes(
                b"fixture", filename="bundle.zip", drive_id=str(self.source.sqid),
            )
        with patch.dict(settings.ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES, {
            "test_fail": f"{__name__}.TestFailingFileExtractor",
        }):
            workflow = load_workflow(
                "angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True,
            )
            run = start_run(workflow, actor=self.actor, subject=file)
            run_until(run)
            with system_context(reason="partial archive review"):
                gate = run.step_runs.get(node_key="gate")
                decision = apps.get_model("decisions.Decision").objects.get(pk=gate.decision_id)
            decide(decision, actor=self.actor, chosen=[str(self.target.sqid)])
            run_until(run)
            with system_context(reason="partial archive results"):
                run.refresh_from_db()
                self.assertEqual((run.status, run.outcome), ("succeeded", "partial"))
                mapped = run.step_runs.get(node_key="import_units")
                self.assertEqual(mapped.map_total, 2)
                self.assertEqual([item["outcome"] for item in mapped.output], ["error", "completed"])
                self.assertEqual(mapped.map_rows().filter(outcome="completed").get().records.count(), 1)

    def test_mixed_target_resources_route_without_a_decision(self):
        with actor_context(self.actor):
            file = apps.get_model("storage.File").objects.ingest_bytes(
                b"fixture", filename="bundle.zip", drive_id=str(self.source.sqid),
            )
        with patch.dict(settings.ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES, {
            "test_other_target": f"{__name__}.TestOtherTargetExtractor",
        }):
            workflow = load_workflow(
                "angee.workflows_integrate.archive_import_file", actor=self.actor, allow_non_dev=True,
            )
            run = start_run(workflow, actor=self.actor, subject=file)
            run_until(run)
            with system_context(reason="mixed archive target assertion"):
                run.refresh_from_db()
                self.assertEqual((run.status, run.outcome), ("succeeded", "unsupported"))
                gate = run.step_runs.get(node_key="gate")
                self.assertFalse(gate.decision_id is not None)
