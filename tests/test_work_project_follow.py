"""The selected project phase and status drive the source task atomically."""

from datetime import date
from pathlib import Path

from django.core.exceptions import ValidationError
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class ProjectFollowTests(WorkCase):
    def promote(self, task):
        with actor_context(self.owner):
            return self.as_user(task).promote_to_project()

    def phases(self, project):
        with system_context(reason="tests.work.phases"):
            return tuple(
                self.Milestone.objects.create(project=project, name=name, active_stage=self.stages[stage])
                for name, stage in (("Opening", "Active"), ("Delivery", "Final"))
            )

    def assert_stage(self, task, name):
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages[name].pk)

    def test_promotion_phase_history_and_status(self):
        task = self.task(visibility="restricted")
        project = self.promote(task)
        self.assert_stage(task, "Active")
        opening, delivery = self.phases(project)
        with actor_context(self.owner):
            project = self.as_user(project)
            project.target_date = date(2027, 1, 1)
            project.save(update_fields=("target_date", "updated_at"))
            project.set_current_milestone(delivery)
            self.assert_stage(task, "Final")
            delivery.with_actor(self.owner).mark_reached()
            project.complete()
            self.assert_stage(task, "Final")
            project.set_current_milestone(opening)
            self.assert_stage(task, "Active")
            self.assertEqual(project.status, "open")
            delivery.refresh_from_db()
            self.assertIsNotNone(delivery.reached_at)
            project.pause()
            self.assert_stage(task, "Active")
            project.drop()
            self.assert_stage(task, "Canceled")
            project.resume()
            self.assert_stage(task, "Active")
            before = (task.revision, task.updated_at)
            project.sync_source_task_stage()
            task.refresh_from_db()
            self.assertEqual((task.revision, task.updated_at), before)
        self.assertEqual(task.visibility, "restricted")
        self.assertEqual(project.target_date, date(2027, 1, 1))

    def test_absent_cancellation_stage_and_queue_are_noops(self):
        task = self.task()
        project = self.promote(task)
        with system_context(reason="tests.work.no_cancellation_stage"):
            self.stages["Canceled"].delete()
        self.as_user(project).drop()
        self.assert_stage(task, "Active")
        self.Task._base_manager.filter(pk=task.pk).update(queue=None, stage=None)
        self.as_user(project).resume()
        task.refresh_from_db()
        self.assertIsNone(task.stage_id)

    def test_invalid_phase_mapping_rolls_back_phase_and_task(self):
        task = self.task()
        project = self.promote(task)
        opening, delivery = self.phases(project)
        self.as_user(project).set_current_milestone(opening)
        with system_context(reason="tests.work.other_queue"):
            queue = self.Queue.objects.create(name="Other", key="OTHER", owner=self.manager, provision_stages=False)
            foreign = self.Stage.objects.create(queue=queue, name="Active", category="started", rule_owned=True)
            with self.assertRaises(ValidationError):
                self.Milestone.objects.create(project=project, name="Invalid", active_stage=foreign)
        # Simulate an existing invalid configuration; phase selection must remain atomic.
        self.Milestone._base_manager.filter(pk=delivery.pk).update(active_stage=foreign)
        with self.assertRaises(ValidationError):
            self.as_user(project).set_current_milestone(delivery)
        project.refresh_from_db()
        self.assertEqual(project.current_milestone_id, opening.pk)
        self.assert_stage(task, "Active")


def test_work_project_follow(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_project_follow.ProjectFollowTests", app="angee.work")
