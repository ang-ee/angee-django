"""Real HTTP query budgets for the composed Notes example."""

from __future__ import annotations

import json

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rebac import system_context


class NotesQueryBudgetTests(TransactionTestCase):
    """Keep label selection and row authorization on the native query path."""

    def test_display_name_fetches_its_title_with_the_rows(self) -> None:
        """A one-row or 25-row label picker has flat Notes SQL, with scope intact."""

        call_command("rebac", "sync", verbosity=0)
        note_model = apps.get_model("notes", "Note")
        with system_context(reason="notes query budget fixture"):
            owner = get_user_model().objects.create_user(username="label-owner")
            outsider = get_user_model().objects.create_user(username="label-outsider")
            notes = note_model.objects.bulk_create(
                [note_model(title=f"Note {index:02}", created_by=owner, updated_by=owner) for index in range(25)]
            )
            note_model.objects.create(title="Hidden", created_by=outsider, updated_by=outsider)
            self.client.force_login(owner, backend="angee.iam.auth.ModelBackend")
        query = """
            query NoteLabels($limit: Int!) {
              notes(limit: $limit, order_by: [{title: asc}]) { id display_name }
            }
        """
        table = connection.ops.quote_name(note_model._meta.db_table)
        counts = []
        for size in (1, 25):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(
                    "/graphql/public/",
                    data=json.dumps({"query": query, "variables": {"limit": size}}),
                    content_type="application/json",
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.wsgi_request.user.pk, owner.pk)
            payload = response.json()
            self.assertNotIn("errors", payload)
            self.assertEqual(
                payload["data"]["notes"],
                [{"id": str(note.sqid), "display_name": note.title} for note in notes[:size]],
            )
            counts.append(sum(f"FROM {table}" in item["sql"] for item in captured))

        self.assertEqual(counts[0], counts[1], "Reading label titles must not add a query for each row")

    def test_audit_labels_batch_distinct_editors(self) -> None:
        """The real HTTP optimizer batches 25 distinct editors into one relation read."""

        call_command("rebac", "sync", verbosity=0)
        note_model = apps.get_model("notes", "Note")
        user_model = get_user_model()
        with system_context(reason="notes audit label query fixture"):
            owner = user_model.objects.create_user(username="audit-owner")
            editors = [user_model.objects.create_user(username=f"editor-{index}") for index in range(25)]
            note_model.objects.bulk_create(
                [
                    note_model(title=f"Note {index:02}", created_by=owner, updated_by=editor)
                    for index, editor in enumerate(editors)
                ]
            )
            self.client.force_login(owner, backend="angee.iam.auth.ModelBackend")
        query = """
            query NoteAuthors($limit: Int!) {
              notes(limit: $limit, order_by: [{title: asc}]) { created_by_label updated_by_label }
            }
        """
        user_table = connection.ops.quote_name(user_model._meta.db_table)
        counts = []
        for size in (1, 25):
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(
                    "/graphql/public/",
                    data=json.dumps({"query": query, "variables": {"limit": size}}),
                    content_type="application/json",
                )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.wsgi_request.user.pk, owner.pk)
            payload = response.json()
            self.assertNotIn("errors", payload)
            self.assertEqual(
                payload["data"]["notes"],
                [
                    {"created_by_label": owner.username, "updated_by_label": editor.username}
                    for editor in editors[:size]
                ],
            )
            # Session authentication loads the request user separately; count the
            # narrow label projection, not that ordinary authentication read.
            counts.append(
                sum(
                    f"FROM {user_table}" in item["sql"]
                    and "first_name" in item["sql"]
                    and "last_name" in item["sql"]
                    and "password" not in item["sql"]
                    for item in captured
                )
            )
        self.assertEqual(counts, [2, 2])
