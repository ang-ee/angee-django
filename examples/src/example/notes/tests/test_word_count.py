"""Persisted word-count behavior for the notes addon."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, cast

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
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


class NoteWordCountGraphQLTests(TransactionTestCase):
    """Expose ``word_count`` through aggregates and ordering."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.alice = User.objects.get(username="alice")
        self.client = Client()
        self.login("alice")

    def test_note_groups_return_word_count_sum_per_bucket(self) -> None:
        data = self.graphql(
            """
            query {
              notes_groups(group_by: [{field: STATUS}]) {
                key { status }
                aggregate {
                  count
                  sum { word_count }
                }
              }
            }
            """
        )["data"]["notes_groups"]
        expected = {
            str(row["status"]).upper(): str(row["word_count_sum"] or 0)
            for row in Note.objects.as_user(self.alice)
            .values("status")
            .annotate(word_count_sum=Sum("word_count"))
        }

        self.assertEqual(
            {
                _dimension(row, "status"): row["aggregate"]["sum"]["word_count"]
                for row in data
            },
            expected,
        )

    def test_notes_order_by_word_count(self) -> None:
        data = self.graphql(
            """
            query {
              notes(limit: 100, order_by: [{word_count: asc}]) {
                title
                word_count
              }
            }
            """
        )["data"]["notes"]
        counts = [node["word_count"] for node in data]

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


def _dimension(group: dict[str, Any], key: str) -> str:
    """Return a named typed group key value."""

    return cast(str, group["key"][key])
