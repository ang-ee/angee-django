"""A task's hand verbs are offered exactly where their owners admit them."""

from pathlib import Path

from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase

ACTIONS = """query($id: String!) { project_tasks_by_pk(id: $id) { task_actions } }"""
ARGUMENTS = {"drop": ("obsolete",)}
STAGES = ("Triage", "Ready", "Doing", "Completed", "Declined", "Active", "Final", "Canceled")


class _Rollback(Exception):
    """Undo one probing verb run."""


class TaskActionTests(WorkCase):
    def offered(self, task, user=None):
        row = self.graphql(ACTIONS, {"id": str(task.sqid)}, user=user)["project_tasks_by_pk"]
        return set(row["task_actions"])

    def run_verb(self, task, verb):
        """Run ``verb`` as the owner and roll it back; return whether it was admitted and changed the row."""

        before = type(task)._base_manager.values_list("stage_id", "status").get(pk=task.pk)
        try:
            with actor_context(self.owner), transaction.atomic():
                getattr(self.as_user(task), verb)(*ARGUMENTS.get(verb, ()))
                after = type(task)._base_manager.values_list("stage_id", "status").get(pk=task.pk)
                raise _Rollback(after != before)
        except _Rollback as outcome:
            return True, outcome.args[0]
        except (ValidationError, PermissionDenied):
            return False, False

    def test_each_offered_verb_is_admitted_and_each_withheld_verb_refuses_or_changes_nothing(self):
        verbs = self.Task.hand_actions()
        self.assertEqual(verbs, ("complete", "drop", "reopen", "accept", "start", "return_to_triage", "remove"))
        for name in STAGES:
            task = self.task(name)
            offered = self.offered(task)
            for verb in verbs:
                admitted, changed = self.run_verb(task, verb)
                with self.subTest(stage=name, verb=verb, offered=verb in offered):
                    if verb in offered:
                        self.assertTrue(admitted)
                    else:
                        self.assertFalse(admitted and changed)

    def test_rule_owned_stages_offer_no_hand_verb_and_triage_alone_offers_accept(self):
        for name in ("Active", "Final", "Canceled"):
            with self.subTest(stage=name):
                self.assertEqual(self.offered(self.task(name)), set())
        self.assertIn("accept", self.offered(self.task("Triage")))
        for name in ("Ready", "Doing", "Declined"):
            with self.subTest(stage=name):
                self.assertNotIn("accept", self.offered(self.task(name)))
        self.assertEqual(self.offered(self.task("Declined")) & {"reopen", "drop"}, {"reopen", "drop"})

    def test_only_closed_work_is_offered_reopen_and_open_work_refuses_it(self):
        for name in ("Triage", "Ready", "Doing"):
            task = self.task(name)
            with self.subTest(stage=name):
                self.assertNotIn("reopen", self.offered(task))
                with actor_context(self.owner), self.assertRaisesMessage(
                    ValidationError, "Only a closed task can be reopened."
                ):
                    self.as_user(task).reopen()
        for name in ("Completed", "Declined"):
            with self.subTest(stage=name):
                self.assertIn("reopen", self.offered(self.task(name)))

    def test_a_promoted_task_is_not_offered_removal_and_its_verb_refuses(self):
        task = self.task()
        self.assertIn("remove", self.offered(task))
        with actor_context(self.owner):
            self.as_user(task).promote_to_project()
        self.assertNotIn("remove", self.offered(task))
        with actor_context(self.owner), self.assertRaisesMessage(ValidationError, "A promoted task cannot be removed."):
            self.as_user(task).remove()

    def test_missing_queue_stages_withhold_the_verbs_that_need_them(self):
        task = self.task("Doing")
        with system_context(reason="tests.work.task_actions.stages"):
            self.Stage._base_manager.filter(pk=self.stages["Completed"].pk).update(rule_owned=True)
            self.Queue._base_manager.filter(pk=self.queue.pk).update(triage_enabled=False)
        offered = self.offered(task)
        self.assertNotIn("complete", offered)
        self.assertNotIn("return_to_triage", offered)
        with actor_context(self.owner), self.assertRaisesMessage(ValidationError, "Queue has no completed stage."):
            self.as_user(task).complete()

    def test_readers_are_offered_nothing(self):
        task = self.task("Triage")
        self.share(task, self.reader)
        self.assertEqual(self.offered(task, user=self.reader), set())


def test_work_task_actions(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_task_actions.TaskActionTests", app="angee.work")
