"""Removing a task records why; restoring returns it to the stage it held before."""

from pathlib import Path

import pytest
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase

REMOVE = """mutation($task: ID!, $revision: Int!, $reason: String!) {
  remove_task(task: $task, expected_revision: $revision, reason: $reason) { ok message }
}"""
RESTORE = """mutation($selection: [ActionSelectionInput!]!) {
  restore_tasks(selection: $selection) { ok code }
}"""
THREAD = """query($id: ID!) {
  record_thread(input: {model_label: "projects.Task", record_id: $id}) {
    error_code
    messages { preview }
  }
}"""


class TaskRemovalTests(WorkCase):
    def remove(self, task, reason="", user=None):
        task.refresh_from_db()
        variables = {"task": str(task.sqid), "revision": task.revision, "reason": reason}
        return self.graphql(REMOVE, variables, user=user or self.manager)["remove_task"]

    def restore(self, task, user=None):
        task.refresh_from_db()
        selection = [{"id": str(task.sqid), "expected_revision": task.revision}]
        return self.graphql(RESTORE, {"selection": selection}, user=user or self.admin)["restore_tasks"][0]

    def test_remove_records_its_reason_and_restore_returns_the_previous_stage(self):
        task = self.task("Doing")
        self.share(task, self.reader)
        self.assertTrue(self.remove(task, "  Duplicate of an older request  ")["ok"])
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Removed"].pk)
        latest = task.history.order_by("-history_date", "-history_id").first()
        self.assertEqual(latest.history_change_reason, "Duplicate of an older request")
        self.assertFalse(self.scoped(task, self.reader))

        self.assertFalse(self.restore(task, user=self.manager)["ok"])
        self.assertTrue(self.restore(task)["ok"])
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Doing"].pk)
        self.assertEqual(task.status, self.Task.TaskStatus.OPEN)
        self.assertTrue(self.scoped(task, self.reader))
        self.assertFalse(self.restore(task)["ok"])

    def test_restore_returns_a_triage_task_to_triage(self):
        task = self.task("Triage")
        self.assertTrue(self.remove(task)["ok"])
        self.assertTrue(self.restore(task)["ok"])
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Triage"].pk)

    def test_restore_falls_back_to_the_default_stage_when_the_previous_one_is_gone(self):
        task = self.task("Doing")
        self.assertTrue(self.remove(task)["ok"])
        with system_context(reason="tests.work.removal.retire_stage"):
            self.Stage._base_manager.filter(pk=self.stages["Doing"].pk).update(rule_owned=True)
        self.assertTrue(self.restore(task)["ok"])
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Ready"].pk)

    def test_removed_task_takes_its_whole_conversation_from_a_non_manager(self):
        task = self.task()
        self.share(task, self.reader)
        with actor_context(self.owner):
            question = self.as_user(task).message_post("Can this ship today?")
            answer = self.as_user(task).message_post("Yes, after review.", parent=question)
        page = self.graphql(THREAD, {"id": str(task.sqid)}, user=self.reader, bucket="console")["record_thread"]
        conversation = ("Can this ship today?", "Yes, after review.")
        previews = [row["preview"] for row in page["messages"]]
        self.assertEqual([preview for preview in previews if preview in conversation], [*conversation])
        for row in (question, answer, question.thread):
            self.assertTrue(self.scoped(row, self.reader))

        self.assertTrue(self.remove(task, "Out of scope")["ok"])
        hidden = self.graphql(THREAD, {"id": str(task.sqid)}, user=self.reader, bucket="console")["record_thread"]
        self.assertEqual(hidden, {"error_code": "NOT_FOUND", "messages": []})
        for row in (question, answer, question.thread):
            self.assertFalse(self.scoped(row, self.reader))


class TaskRemovalDenormalizedTests(TaskRemovalTests):
    storage = "denormalized"


@pytest.mark.parametrize("test_class", ["TaskRemovalTests", "TaskRemovalDenormalizedTests"])
def test_work_task_removal(tmp_path: Path, test_class: str):
    run_composed_tests(tmp_path, f"tests.test_work_task_removal.{test_class}", app="angee.intake")
