"""History, revisions, and delete-preview on the composed notes addon."""

from __future__ import annotations

import reversion
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context, to_subject_ref
from strawberry import relay

from angee.graphql.schema import GraphQLSchemas

Note = apps.get_model("notes", "Note")
User = get_user_model()


class NotesVersioningTests(TransactionTestCase):
    """Audit history, body revisions, and cascade-previewing deletes."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="test-setup"):
            self.user = User.objects.create(username="ed", email="ed@example.com", password="!")
            self.note = Note(title="Draft", body="v1", created_by=self.user)
            self.note.save()

    def test_history_records_each_change(self) -> None:
        with system_context(reason="test"):
            self.note.title = "Final"
            self.note.save()
        self.assertEqual(self.note.history.count(), 2)
        self.assertEqual(self.note.history.first().title, "Final")

    def test_body_revisions_version_and_revert(self) -> None:
        with system_context(reason="test"):
            with reversion.create_revision():
                self.note.body = "v1"
                self.note.save()
            with reversion.create_revision():
                self.note.body = "v2"
                self.note.save()

            self.assertEqual(self.note.revisions.count(), 2)
            self.note.revert_to(self.note.revisions.last())
            self.note.refresh_from_db()

        self.assertEqual(self.note.body, "v1")

    def test_delete_returns_a_cascade_preview(self) -> None:
        query = (
            "mutation($id: ID!){ deleteNote(id: $id, confirm: true)"
            "{ totalDeletedCount hasBlockers deleted { label count } } }"
        )
        with actor_context(to_subject_ref(self.user)):
            schema = GraphQLSchemas.from_discovery().build("console")
            result = schema.execute_sync(
                query,
                variable_values={"id": relay.to_base64("NoteType", self.note.sqid)},
            )

        self.assertIsNone(result.errors)
        self.assertEqual(result.data["deleteNote"]["totalDeletedCount"], 1)
        self.assertFalse(result.data["deleteNote"]["hasBlockers"])
        with system_context(reason="test"):
            self.assertFalse(Note.objects.filter(sqid=self.note.sqid).exists())
