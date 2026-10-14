"""The composed GraphQL surface for record tasks: schedule, list, complete and the catalog."""

from pathlib import Path
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase

LIST = """
    query Tasks($model: String!, $id: ID!) {
      record_tasks(model_label: $model, record_id: $id) {
        id title status activity_type setup_state overdue_milestone_count
        assignee { id } subject { label model_label record_id } task_actions
      }
    }
"""
SCHEDULE = """
    mutation Schedule($model: String!, $id: ID!, $title: String!, $assignee: ID, $due: Date) {
      schedule_record_task(input: {
        model_label: $model, record_id: $id, title: $title, assignee: $assignee, due_date: $due,
        activity_type: "call"
      }) { ok message id }
    }
"""


def test_record_task_graphql_surface(tmp_path: Path) -> None:
    run_composed_tests(tmp_path, "tests.test_projects_record_tasks_graphql.RecordTaskGraphQLTests", app="angee.work")


class RecordTaskGraphQLTests(WorkCase):
    def setUp(self):
        super().setUp()
        with system_context(reason="tests.record_tasks.setup"):
            activity_types = apps.get_model("projects", "ActivityType")
            activity_types.objects.create(key="todo", name="To do", glyph="circle-check")
            activity_types.objects.create(key="call", name="Call", glyph="phone")
            self.record_row = self.Project.objects.create(title="Supplier onboarding", owner=self.owner)
        self.share(self.record_row, self.reader)
        self.share(self.record_row, self.assignee)
        self.record = {"model": "projects.Project", "id": self.record_row.sqid}

    def raw(self, query, variables, user):
        """Execute one console document as ``user`` and return the whole result, errors included."""

        from angee.graphql.schema import GraphQLSchemas

        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            return GraphQLSchemas.from_discovery().build("console").execute_sync(
                query, variable_values=variables, context_value=SimpleNamespace(request=request),
            )

    def schedule(self, title, due):
        scheduled = self.graphql(
            SCHEDULE, {**self.record, "title": title, "assignee": self.assignee.sqid, "due": due}, bucket="console",
        )["schedule_record_task"]
        self.assertTrue(scheduled["ok"], scheduled["message"])
        return scheduled["id"]

    def test_schedule_list_and_complete_through_the_generic_task_verb(self):
        self.schedule("Confirm the bank change", "2026-03-05")
        (row,) = self.graphql(LIST, self.record, user=self.reader, bucket="console")["record_tasks"]
        self.assertEqual(row["subject"], {
            "label": "Supplier onboarding", "model_label": "projects.Project", "record_id": self.record_row.sqid,
        })
        self.assertEqual((row["activity_type"], row["assignee"]["id"]), ("call", self.assignee.sqid))
        # A reader of the record reads its task but closes nothing; its writer closes it.
        self.assertEqual(row["task_actions"], [])
        (own,) = self.graphql(LIST, self.record, bucket="console")["record_tasks"]
        self.assertTrue({"complete", "drop"} <= set(own["task_actions"]), own["task_actions"])
        kinds = self.graphql("query Kinds { activity_types { key } }", {}, user=self.reader, bucket="console")
        self.assertEqual({kind["key"] for kind in kinds["activity_types"]}, {"todo", "call"})

        completed = self.graphql("""
            mutation Complete($id: ID!) { complete_task(id: $id, feedback: "Confirmed.") { ok message } }
        """, {"id": row["id"]}, bucket="console")["complete_task"]
        self.assertTrue(completed["ok"], completed["message"])
        task = self.Task._base_manager.get(title="Confirm the bank change")
        self.assertEqual(task.status, "done")

    def test_the_list_is_ordered_server_side_and_costs_the_same_for_more_rows(self):
        self.schedule("Later", "2026-05-01")

        def listed():
            with CaptureQueriesContext(connection) as queries:
                rows = self.graphql(LIST, self.record, user=self.reader, bucket="console")["record_tasks"]
            return [row["title"] for row in rows], len(queries)

        listed()  # Warm the per-process caches the first request fills.
        titles, one = listed()
        self.assertEqual(titles, ["Later"])
        self.schedule("Sooner", "2026-04-01")
        self.schedule("Also sooner", "2026-04-01")
        titles, three = listed()
        self.assertEqual(titles, ["Also sooner", "Sooner", "Later"])
        self.assertEqual(three, one)

    def test_a_reader_cannot_schedule_and_an_outsider_lists_nothing(self):
        refused = self.raw("""
            mutation Schedule($model: String!, $id: ID!) {
              schedule_record_task(input: {model_label: $model, record_id: $id, title: "No"}) { ok }
            }
        """, self.record, self.reader)
        self.assertTrue(refused.errors)
        self.assertEqual(refused.errors[0].extensions.get("code"), "PERMISSION_DENIED")
        self.schedule("Hidden", "2026-03-05")
        hidden = self.raw(
            "query T($model: String!, $id: ID!) { record_tasks(model_label: $model, record_id: $id) { id } }",
            self.record, self.outsider,
        )
        self.assertFalse(hidden.errors, hidden.errors)
        self.assertEqual(hidden.data["record_tasks"], [])


    def test_a_record_type_that_takes_no_tasks_is_refused_naming_those_that_do(self):
        person = {"model": "parties.Person", "id": self.Person._base_manager.get(user=self.owner).sqid}
        with self.assertNoLogs("angee.graphql.schema", level="ERROR"):
            listed = self.raw(LIST, person, self.owner)
            scheduled = self.raw(SCHEDULE, {**person, "title": "No"}, self.owner)
        # A task's subject edge declares which record types take tasks; chatter is not the gate.
        self.assertEqual([error.extensions.get("code") for error in listed.errors], ["VALIDATION"])
        self.assertIn("Task may target only", listed.errors[0].message)
        self.assertFalse(scheduled.errors, scheduled.errors)
        self.assertFalse(scheduled.data["schedule_record_task"]["ok"])
        self.assertIn("Task may target only", scheduled.data["schedule_record_task"]["message"])
