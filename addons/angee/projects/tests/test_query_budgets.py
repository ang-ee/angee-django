"""HTTP query budgets against the actual composed Projects models and schemas."""

from __future__ import annotations

import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rebac import system_context


class TaskLabelQueryBudgetTests(TransactionTestCase):
    """Public and console task labels share the model title without deferred reads."""

    def test_narrow_labels_preserve_authorization_in_both_schemas(self) -> None:
        """One and 25 tasks use the same SQL count, and private rows stay hidden."""

        call_command("rebac", "sync", verbosity=0)
        task_model = apps.get_model("projects", "Task")
        with system_context(reason="projects label query budget fixture"):
            owner = get_user_model().objects.create_user(username="task-label-owner")
            outsider = get_user_model().objects.create_user(username="task-label-outsider")
            tasks = task_model.objects.bulk_create(
                [
                    task_model(
                        title=f"Task {index:02}",
                        created_by=owner,
                        updated_by=owner,
                        sort_order=float(index),
                        sub_sort_order=float(index),
                    )
                    for index in range(25)
                ]
            )
            hidden = task_model.objects.create(title="Hidden", created_by=outsider, updated_by=outsider)
            self.client.force_login(owner, backend="angee.iam.auth.ModelBackend")
        query = """
            query TaskLabels($limit: Int!) {
              project_tasks(limit: $limit, order_by: [{title: asc}]) { id display_name }
            }
        """
        table = connection.ops.quote_name(task_model._meta.db_table)
        for schema_name in ("public", "console"):
            with self.subTest(schema=schema_name):
                counts = []
                for size in (1, 25):
                    with CaptureQueriesContext(connection) as captured:
                        response = self.client.post(
                            f"/graphql/{schema_name}/",
                            data=json.dumps({"query": query, "variables": {"limit": size}}),
                            content_type="application/json",
                        )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.wsgi_request.user.pk, owner.pk)
                    self.assertEqual(
                        response.json(),
                        {
                            "data": {
                                "project_tasks": [
                                    {"id": str(task.sqid), "display_name": task.title} for task in tasks[:size]
                                ]
                            }
                        },
                    )
                    counts.append(sum(f"FROM {table}" in item["sql"] for item in captured))
                self.assertEqual(counts[0], counts[1], f"Task label SQL grew with the page: {counts}")

                detail = """
                    query TaskLabel($id: String!) {
                      project_tasks_by_pk(id: $id) { id display_name }
                    }
                """
                response = self.client.post(
                    f"/graphql/{schema_name}/",
                    data=json.dumps({"query": detail, "variables": {"id": str(hidden.sqid)}}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"data": {"project_tasks_by_pk": None}})
