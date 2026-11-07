"""Record merges preserve task intent, moved evidence and one reversible boundary."""

from pathlib import Path

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class RecordMergeTests(WorkCase):
    def test_duplicate_triage_moves_need_and_keeps_original_target_in_the_same_changeset(self):
        source, canonical = self.task("Triage"), self.task("Ready")
        Need = apps.get_model("intake", "Need")
        with system_context(reason="tests.work.merge.need"):
            # Existing evidence without a pending admission question.
            [need] = Need._base_manager.bulk_create([Need(task=source, body="Original request")])
        with actor_context(self.owner):
            self.as_user(source).mark_duplicate(canonical)
        source.refresh_from_db()
        need.refresh_from_db()
        self.assertEqual((source.merged_into_id, source.is_trashed), (canonical.pk, True))
        self.assertEqual((source.stage.category, source.dropped_reason), ("duplicate", "duplicate"))
        self.assertEqual(need.task_id, canonical.pk)
        self.assertIn(source.pk, need.history.values_list("task_id", flat=True))
        task_version = source.history.order_by("-history_id").first()
        need_version = need.history.order_by("-history_id").first()
        self.assertEqual(need_version.changeset_id, task_version.changeset_id)
        self.assertEqual(task_version.changeset.activity, "merge")
        self.assertFalse(apps.get_model("projects", "TaskRelation")._base_manager.exists())

    def test_a_fold_refuses_what_removal_refuses_before_anything_moves(self):
        source, canonical = self.task("Triage"), self.task("Ready")
        Need = apps.get_model("intake", "Need")
        Project = apps.get_model("projects", "Project")
        with system_context(reason="tests.work.merge.promoted"):
            [need] = Need._base_manager.bulk_create([Need(task=source, body="Promoted request")])
            Project._base_manager.create(title="Promoted", converted_from=source, owner=self.owner)
        blocked = {
            task.pk
            for condition, _message in self.Task.merge_blockers()
            for task in self.Task._base_manager.filter(condition)
        }
        self.assertEqual(blocked, {source.pk})
        with actor_context(self.owner), self.assertRaisesMessage(ValidationError, "promoted task cannot be removed"):
            self.as_user(source).mark_duplicate(canonical)
        source.refresh_from_db()
        need.refresh_from_db()
        self.assertEqual((source.merged_into_id, source.is_trashed, need.task_id), (None, False, source.pk))

    def test_duplicate_merge_requires_write_on_both_tasks(self):
        source = self.task("Triage", owner=self.outsider, visibility="restricted")
        canonical = self.task("Ready", visibility="restricted")
        self.share(canonical, self.outsider)
        with actor_context(self.outsider), self.assertRaises(PermissionDenied):
            self.as_user(source, self.outsider).mark_duplicate(canonical)
        source.refresh_from_db()
        self.assertIsNone(source.merged_into_id)
        self.assertFalse(source.is_trashed)


def test_work_record_merge(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_record_merge.RecordMergeTests", app="angee.intake")
