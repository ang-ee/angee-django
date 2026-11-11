"""A task's stage label survives redaction of its operational queue."""

from types import SimpleNamespace

from django.test import RequestFactory
from rebac import actor_context

from angee.graphql.schema import GraphQLSchemas
from tests.native_work import WorkCase


class TaskStatusTests(WorkCase):
    def test_task_reader_gets_status_without_queue_access(self):
        task = self.task(stage="Ready")
        self.share(task, self.reader)
        for schema_name in ("public", "console"):
            request = RequestFactory().post(f"/graphql/{schema_name}/")
            request.user = self.reader
            with actor_context(self.reader):
                result = (
                    GraphQLSchemas.from_discovery()
                    .build(schema_name)
                    .execute_sync(
                        "query Status($id: String!) { project_tasks_by_pk(id: $id) {"
                        " stage_name stage { id } queue { id } } }",
                        variable_values={"id": str(task.sqid)},
                        context_value=SimpleNamespace(request=request),
                    )
                )
            self.assertIsNone(result.errors, result.errors)
            self.assertEqual(result.data["project_tasks_by_pk"], {"stage_name": "Ready", "stage": None, "queue": None})
