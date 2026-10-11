"""The composed GraphQL surface for tasks about a record: schedule, list, complete, agenda, catalog."""

from pathlib import Path
from types import SimpleNamespace

from django.apps import apps
from django.test import RequestFactory
from rebac import actor_context, system_context

from tests.composed_host import run_composed_tests
from tests.native_work import WorkCase


def test_card_task_graphql_surface(tmp_path: Path) -> None:
    run_composed_tests(tmp_path, "tests.test_projects_card_tasks_graphql.CardTaskGraphQLTests", app="angee.work")


class CardTaskGraphQLTests(WorkCase):
    def setUp(self):
        super().setUp()
        with system_context(reason="tests.card_tasks.setup"):
            activity_types = apps.get_model("projects", "ActivityType")
            activity_types.objects.create(key="todo", name="To do", glyph="circle-check")
            activity_types.objects.create(key="call", name="Call", glyph="phone")
            self.card = self.Project.objects.create(title="Supplier onboarding", owner=self.owner)
        self.share(self.card, self.reader)
        self.record = {"model": "projects.Project", "id": self.card.sqid}

    def raw(self, query, variables, user):
        """Execute one console document as ``user`` and return the whole result, errors included."""

        from angee.graphql.schema import GraphQLSchemas

        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            return GraphQLSchemas.from_discovery().build("console").execute_sync(
                query, variable_values=variables, context_value=SimpleNamespace(request=request),
            )

    def test_schedule_list_complete_and_agenda(self):
        scheduled = self.graphql("""
            mutation Schedule($model: String!, $id: ID!, $assignee: ID) {
              schedule_record_task(input: {
                model_label: $model, record_id: $id, title: "Confirm the bank change", assignee: $assignee,
                due_date: "2026-03-05", activity_type: "call"
              }) { ok message id }
            }
        """, {**self.record, "assignee": self.assignee.sqid}, bucket="console")["schedule_record_task"]
        self.assertTrue(scheduled["ok"], scheduled["message"])

        listed = self.graphql("""
            query Tasks($model: String!, $id: ID!) {
              record_tasks(model_label: $model, record_id: $id) {
                id title status activity_type subject { label model_label record_id }
              }
              activity_types { key }
            }
        """, self.record, user=self.reader, bucket="console")
        (row,) = listed["record_tasks"]
        self.assertEqual(row["subject"], {
            "label": "Supplier onboarding", "model_label": "projects.Project", "record_id": self.card.sqid,
        })
        self.assertEqual(row["activity_type"], "call")
        self.assertEqual({kind["key"] for kind in listed["activity_types"]}, {"todo", "call"})

        agenda = self.graphql("""
            query Agenda { task_agenda(window_start: "2026-03-01", window_end: "2026-04-01") { title } }
        """, {}, user=self.assignee, bucket="console")["task_agenda"]
        self.assertEqual(agenda, [{"title": "Confirm the bank change"}])

        completed = self.graphql("""
            mutation Complete($id: ID!) { complete_record_task(id: $id, feedback: "Confirmed.") { ok message } }
        """, {"id": row["id"]}, bucket="console")["complete_record_task"]
        self.assertTrue(completed["ok"], completed["message"])
        task = self.Task._base_manager.get(title="Confirm the bank change")
        self.assertEqual(task.status, "done")

    def test_a_reader_cannot_schedule_and_an_outsider_cannot_list(self):
        refused = self.raw("""
            mutation Schedule($model: String!, $id: ID!) {
              schedule_record_task(input: {model_label: $model, record_id: $id, title: "No"}) { ok }
            }
        """, self.record, self.reader)
        self.assertTrue(refused.errors)
        self.assertEqual(refused.errors[0].extensions.get("code"), "PERMISSION_DENIED")
        hidden = self.raw(
            "query T($model: String!, $id: ID!) { record_tasks(model_label: $model, record_id: $id) { id } }",
            self.record, self.outsider,
        )
        self.assertTrue(hidden.errors, hidden.data)
