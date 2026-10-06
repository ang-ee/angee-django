"""A queue's stages are owned lines: saved with the queue, and system stages stay locked."""

from pathlib import Path
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import RequestFactory
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase

_READ = """query Queue($id: String!) {
  work_queues_by_pk(id: $id) { id stages { id name category tone position rule_owned conceals locked_fields } }
}"""

_SAVE = """mutation SaveQueue($pk: ID!, $lines: [work_queues_stages_insert_input!]) {
  work_queues_save(pk: $pk, lines: $lines) { id stages { id name position } }
}"""

_INSERT = """mutation CreateQueue($object: work_queues_insert_input!) {
  insert_work_queues_one(object: $object) { id stages { name category position } }
}"""


class StageLinesTests(WorkCase):
    def execute(self, query, variables, *, user=None):
        """Execute one composed-schema document as ``user`` and return the raw result."""

        from angee.graphql.schema import GraphQLSchemas

        user = user or self.manager
        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            return (
                GraphQLSchemas.from_discovery()
                .build("public")
                .execute_sync(query, variable_values=variables, context_value=SimpleNamespace(request=request))
            )

    def lines(self, **changes):
        """Return the queue's stored stages as a full lines payload, with per-name changes."""

        rows = self.graphql(_READ, {"id": self.queue.sqid}, user=self.manager)["work_queues_by_pk"]["stages"]
        payload = []
        for index, row in enumerate(rows):
            line = {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"].lower(),
                "tone": row["tone"].lower(),
                "position": index,
                "rule_owned": row["rule_owned"],
                "conceals": row["conceals"],
            }
            line.update(changes.get(row["name"], {}))
            payload.append(line)
        return payload

    def stored(self):
        with system_context(reason="tests.work.stage_lines.read"):
            return list(
                self.Stage._base_manager.filter(queue=self.queue)
                .order_by("position", "pk")
                .values_list("name", "category", "position")
            )

    def assert_refused(self, lines, message):
        before = self.stored()
        result = self.execute(_SAVE, {"pk": self.queue.sqid, "lines": lines})
        self.assertTrue(result.errors, "the lines save was accepted")
        self.assertIn(message, result.errors[0].message)
        self.assertEqual(self.stored(), before)

    def test_stages_round_trip_with_their_locks(self):
        rows = self.graphql(_READ, {"id": self.queue.sqid}, user=self.manager)["work_queues_by_pk"]["stages"]
        self.assertEqual([row["name"] for row in rows], list(self.stages))
        locks = {row["name"]: row["locked_fields"] for row in rows}
        self.assertEqual(locks["Triage"], ["name", "category"])
        self.assertEqual(locks["Duplicate"], ["name", "category"])
        self.assertEqual(locks["Ready"], [])
        payload = self.lines()
        result = self.graphql(_SAVE, {"pk": self.queue.sqid, "lines": payload}, user=self.manager)
        self.assertEqual([row["name"] for row in result["work_queues_save"]["stages"]], list(self.stages))

    def test_positions_persist_from_row_order(self):
        payload = self.lines()
        ready = next(line for line in payload if line["name"] == "Ready")
        payload.remove(ready)
        payload.insert(3, ready)
        payload.append({"name": "Review", "category": "started", "tone": "info", "position": len(payload)})
        for index, line in enumerate(payload):
            line["position"] = index
        self.graphql(_SAVE, {"pk": self.queue.sqid, "lines": payload}, user=self.manager)
        self.assertEqual(
            [(name, position) for name, _category, position in self.stored()],
            [(line["name"], index) for index, line in enumerate(payload)],
        )

    def test_custom_stages_edit_and_remove_through_lines(self):
        edited = self.lines(Doing={"name": "Building", "tone": "success"})
        payload = [line for line in edited if line["name"] != "Declined"]
        self.graphql(_SAVE, {"pk": self.queue.sqid, "lines": payload}, user=self.manager)
        names = [name for name, _category, _position in self.stored()]
        self.assertIn("Building", names)
        self.assertNotIn("Declined", names)
        with system_context(reason="tests.work.stage_lines.read"):
            self.assertEqual(self.Stage._base_manager.get(name="Building").tone, "success")

    def test_lines_cannot_rename_or_recategorize_a_system_stage(self):
        self.assert_refused(self.lines(Triage={"name": "Inbox"}), "cannot change")
        self.assert_refused(self.lines(Duplicate={"category": "canceled"}), "cannot change")

    def test_lines_cannot_delete_a_system_stage(self):
        payload = [line for line in self.lines() if line["name"] != "Duplicate"]
        self.assert_refused(payload, "cannot be deleted")

    def test_lines_cannot_create_or_convert_into_a_system_stage(self):
        self.assert_refused(self.lines(Ready={"category": "triage"}), "system-provisioned")
        payload = self.lines()
        payload.append({"name": "Second triage", "category": "triage", "tone": "warning", "position": len(payload)})
        self.assert_refused(payload, "system-provisioned")

    def test_a_system_stage_tone_and_order_stay_editable(self):
        payload = self.lines(Triage={"tone": "danger"})
        triage = payload.pop(0)
        payload.insert(1, triage)
        for index, line in enumerate(payload):
            line["position"] = index
        self.graphql(_SAVE, {"pk": self.queue.sqid, "lines": payload}, user=self.manager)
        with system_context(reason="tests.work.stage_lines.read"):
            triage = self.Stage._base_manager.get(pk=self.stages["Triage"].pk)
        self.assertEqual((triage.name, triage.tone, triage.position), ("Triage", "danger", 1))

    def test_lines_keep_the_stage_validations(self):
        self.assert_refused(self.lines(Ready={"rule_owned": True, "conceals": True}), "cannot conceal")
        with system_context(reason="tests.work.stage_lines.default"):
            self.Queue._base_manager.filter(pk=self.queue.pk).update(default_stage=self.stages["Ready"])
        self.assert_refused(self.lines(Ready={"conceals": True}), "default stage")

    def test_a_reader_cannot_save_stage_lines(self):
        variables = {"pk": self.queue.sqid, "lines": self.lines(Ready={"name": "Mine"})}
        result = self.execute(_SAVE, variables, user=self.member)
        self.assertTrue(result.errors)
        self.assertNotIn("Mine", [name for name, _category, _position in self.stored()])

    def test_user_writes_outside_lines_keep_the_lock(self):
        with actor_context(self.manager):
            triage = self.as_user(self.stages["Triage"], self.manager)
            triage.name = "Inbox"
            with self.assertRaises(ValidationError):
                triage.save()
            with self.assertRaises(ValidationError):
                self.as_user(self.stages["Duplicate"], self.manager).delete()
        with system_context(reason="tests.work.stage_lines.provisioning"):
            system = self.Stage._base_manager.get(pk=self.stages["Triage"].pk)
            system.name = "Inbox"
            system.save()
        self.assertEqual(self.stored()[0][0], "Inbox")

    def test_nested_insert_keeps_the_provisioned_stages(self):
        created = self.graphql(
            _INSERT,
            {
                "object": {
                    "name": "Support",
                    "key": "SUP",
                    "stages": {"data": [{"name": "Waiting", "category": "started", "tone": "info", "position": 0}]},
                }
            },
            user=self.manager,
        )["insert_work_queues_one"]
        names = [row["name"] for row in created["stages"]]
        self.assertIn("Waiting", names)
        self.assertIn("Triage", names)
        self.assertIn("Duplicate", names)

    def test_queue_resource_advertises_the_locked_stage_lines(self):
        from angee.graphql.schema import GraphQLSchemas

        schema = GraphQLSchemas.from_discovery().build("public")
        (resource,) = [item for item in schema.angee_resources if item.model_label == "work.Queue"]
        self.assertEqual(resource.lines.field, "stages")
        self.assertEqual(resource.lines.position_field, "position")
        self.assertEqual(resource.lines.lock_field, "locked_fields")
        widgets = {field.name: field.widget for field in resource.lines.fields}
        self.assertEqual(widgets["tone"], "tone")
        self.assertEqual(resource.roots.save_name, "work_queues_save")


def test_work_stage_lines(tmp_path: Path):
    run_composed_tests(tmp_path, "tests.test_work_stage_lines.StageLinesTests", app="angee.work")
