"""Persisted word-count behavior for the notes addon."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Sum
from django.test import Client, TransactionTestCase
from rebac import system_context

Note = apps.get_model("notes", "Note")
User = get_user_model()


class NoteWordCountModelTests(TransactionTestCase):
    """Persist the derived ``word_count`` column from model saves."""

    def test_save_sets_word_count_from_body(self) -> None:
        with system_context(reason="test"):
            note = Note.objects.create(title="Counted", body="one two\nthree")
            self.assertEqual(note.word_count, 3)
            old_updated_at = note.updated_at - timedelta(days=1)
            Note.objects.filter(pk=note.pk).update(updated_at=old_updated_at)

            note.body = "single"
            note.save(update_fields={"body"})
            note.refresh_from_db()

        self.assertEqual(note.word_count, 1)
        self.assertGreater(note.updated_at, old_updated_at)


class NoteWordCountMigrationTests(TransactionTestCase):
    """Backfill legacy rows after the schema column exists."""

    def test_backfill_populates_existing_rows(self) -> None:
        migrate_from = [("notes", "0002_historicalnote_word_count_note_word_count")]
        migrate_to = [("notes", "0003_backfill_note_word_count")]

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_from)
        old_apps = executor.loader.project_state(migrate_from).apps
        OldNote = old_apps.get_model("notes", "Note")
        legacy = OldNote.objects.create(
            title="Legacy note",
            body="legacy words counted",
            word_count=0,
        )

        executor = MigrationExecutor(connection)
        executor.migrate(migrate_to)
        new_apps = executor.loader.project_state(migrate_to).apps
        NewNote = new_apps.get_model("notes", "Note")

        self.assertEqual(NewNote.objects.get(pk=legacy.pk).word_count, 3)


class NoteWordCountGraphQLTests(TransactionTestCase):
    """Expose ``word_count`` through aggregates and ordering."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", "demo", allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.alice = User.objects.get(username="alice")
        self.client = Client()
        self.login("alice")

    def test_note_groups_return_word_count_sum_per_bucket(self) -> None:
        data = self.graphql(
            """
            query {
              noteGroups(groupBy: [{field: STATUS}]) {
                results {
                  key { status }
                  count
                  sum { wordCount }
                }
              }
            }
            """
        )["data"]["noteGroups"]
        expected = {
            str(row["status"]).upper(): str(row["word_count_sum"] or 0)
            for row in Note.objects.as_user(self.alice)
            .values("status")
            .annotate(word_count_sum=Sum("word_count"))
        }

        self.assertEqual(
            {row["key"]["status"]: row["sum"]["wordCount"] for row in data["results"]},
            expected,
        )

    def test_notes_order_by_word_count(self) -> None:
        data = self.graphql(
            """
            query {
              notes(pagination: {limit: 100}, order: {wordCount: ASC}) {
                results { title wordCount }
              }
            }
            """
        )["data"]["notes"]["results"]
        counts = [node["wordCount"] for node in data]

        self.assertGreater(len(counts), 1)
        self.assertEqual(counts, sorted(counts))

    def login(self, username: str) -> None:
        result = self.post(
            """
            mutation Login($username: String!, $password: String!) {
              login(username: $username, password: $password) { ok }
            }
            """,
            {"username": username, "password": username},
        )
        self.assertTrue(result["data"]["login"]["ok"])

    def graphql(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.post(query, variables)
        self.assertNotIn("errors", payload)
        return payload

    def post(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            "/graphql/public/",
            data=json.dumps({"query": query, "variables": variables or {}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return cast(dict[str, Any], json.loads(response.content))
