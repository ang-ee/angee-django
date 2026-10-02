"""Stage-owned invariants protect defaults, phase mappings and hand transitions."""

from pathlib import Path
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import RequestFactory
from rebac import actor_context, system_context

from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


class RuleStageTests(WorkCase):
    def test_stage_path_classification_is_owned_by_the_stage(self):
        self.assertTrue(self.stages["Ready"].on_path)
        self.assertTrue(self.stages["Active"].on_path)
        self.assertFalse(self.stages["Removed"].on_path)

    def test_generated_update_refuses_rule_stage(self):
        task = self.task()
        request = RequestFactory().post("/graphql/")
        request.user = self.moderator
        with actor_context(self.moderator):
            result = (
                GraphQLSchemas.from_discovery()
                .build("public")
                .execute_sync(
                    """mutation MoveTask($id: String!, $stage: ID!) {
                    update_project_tasks_by_pk(pk_columns: {id: $id}, _set: {stage: $stage}) { id }
                }""",
                    variable_values={"id": str(task.sqid), "stage": str(self.stages["Active"].sqid)},
                    context_value=SimpleNamespace(request=request),
                )
            )
        self.assertTrue(result.errors)
        self.assertIn("rule-owned stage", result.errors[0].message)
        task.refresh_from_db()
        self.assertEqual(task.stage_id, self.stages["Ready"].pk)

    def test_direct_entry_and_exit_and_hand_verbs_are_refused(self):
        ordinary = self.task()
        reserved = self.task("Active")
        with actor_context(self.owner):
            for task, stage in ((ordinary, "Active"), (reserved, "Ready")):
                with self.subTest(stage=stage), self.assertRaises(ValidationError):
                    changed = self.as_user(task)
                    changed.stage = self.stages[stage]
                    changed.save(update_fields=("stage", "updated_at"))
        for elevated in (False, True):
            context = system_context(reason="tests.work.hand_verbs") if elevated else actor_context(self.owner)
            with context:
                unchanged = self.as_user(reserved)
                before = (unchanged.stage_id, unchanged.revision)
                unchanged.start()
                self.assertEqual((unchanged.stage_id, unchanged.revision), before)
                for name, args in (
                    ("complete", ()),
                    ("drop", ("obsolete",)),
                    ("reopen", ()),
                    ("accept", ()),
                    ("decline", ("declined",)),
                    ("return_to_triage", ()),
                    ("mark_duplicate", (ordinary,)),
                ):
                    with self.subTest(elevated=elevated, verb=name), self.assertRaises(ValidationError):
                        getattr(self.as_user(reserved), name)(*args)

    def test_manual_categories_skip_reserved_and_concealing_stages(self):
        task = self.task()
        self.Stage._base_manager.filter(rule_owned=True).update(position=1)
        self.Stage._base_manager.filter(conceals=True).update(position=0)
        with actor_context(self.owner):
            self.as_user(task).complete()
            task.refresh_from_db()
            self.assertEqual(task.stage_id, self.stages["Completed"].pk)
            self.as_user(task).reopen()
            self.as_user(task).drop("obsolete")
            task.refresh_from_db()
            self.assertEqual(task.stage_id, self.stages["Declined"].pk)
            triage = self.task("Triage")
            self.as_user(triage).accept()
            triage.refresh_from_db()
            self.assertEqual(triage.stage_id, self.stages["Ready"].pk)
            self.as_user(triage).return_to_triage()
            triage.refresh_from_db()
            self.assertEqual(triage.stage_id, self.stages["Triage"].pk)

    def test_default_and_rule_flags_reject_concealment_in_clean_and_save(self):
        for operation in ("clean", "save"):
            with system_context(reason="tests.work.default_stage"):
                queue = self.Queue._base_manager.get(pk=self.queue.pk)
                queue.default_stage = self.stages["Removed"]
                with self.assertRaises(ValidationError):
                    getattr(queue, operation)()
                queue.default_stage = self.stages["Ready"]
                queue.save(update_fields=("default_stage", "updated_at"))
                for name in ("Ready", "Active"):
                    stage = self.Stage._base_manager.get(pk=self.stages[name].pk)
                    stage.conceals = True
                    with self.subTest(operation=operation, stage=name), self.assertRaises(ValidationError):
                        getattr(stage, operation)()
        with system_context(reason="tests.work.invalid_stage"), self.assertRaises(ValidationError):
            self.Stage.objects.create(queue=self.queue, name="Invalid", rule_owned=True, conceals=True)

    def test_stage_edits_preserve_mappings_and_promoted_tasks(self):
        task = self.task()
        with actor_context(self.owner):
            project = self.as_user(task).promote_to_project()
        with system_context(reason="tests.work.stage_configuration"):
            self.Milestone.objects.create(project=project, name="Delivery", active_stage=self.stages["Final"])
            other = self.Queue.objects.create(name="Other", key="OTHER", owner=self.manager, provision_stages=False)
            for fields in ({"rule_owned": False}, {"conceals": True}, {"queue": other}):
                for operation in ("clean", "save"):
                    stage = self.Stage._base_manager.get(pk=self.stages["Final"].pk)
                    for name, value in fields.items():
                        setattr(stage, name, value)
                    with self.subTest(fields=fields, operation=operation), self.assertRaises(ValidationError):
                        getattr(stage, operation)()
            # Older rows may lack rule ownership: promotion still prevents a flag flip.
            self.Task._base_manager.filter(pk=task.pk).update(stage=self.stages["Doing"])
            for operation in ("clean", "save"):
                stage = self.Stage._base_manager.get(pk=self.stages["Doing"].pk)
                stage.conceals = True
                with self.assertRaises(ValidationError):
                    getattr(stage, operation)()
            self.Stage._base_manager.filter(pk=self.stages["Doing"].pk).update(conceals=True)
            legacy = self.Task._base_manager.get(pk=task.pk)
            legacy.title = "Updated title"
            legacy.save(update_fields=("title", "updated_at"))
            self.assertEqual(self.Task._base_manager.get(pk=task.pk).title, "Updated title")


def test_work_rule_stages(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_rule_stages.RuleStageTests", app="angee.work")
