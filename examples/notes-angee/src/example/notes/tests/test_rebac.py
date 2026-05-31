"""Authorization behaviour of the composed notes addon.

These exercise the real runtime model against the permission schema: demo
resources seed users and notes, ownership signals grant the creators access,
and field permissions redact owner-only fields from other readers.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    system_context,
    write_relationships,
)
from rebac.resources import model_resource_type

Note = apps.get_model("notes", "Note")
User = get_user_model()


class NotesAuthorizationTests(TransactionTestCase):
    """Drive the demo resources through the permission schema."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command(
            "resources", "load", "demo", allow_non_dev=True, verbosity=0
        )
        with system_context(reason="test-setup"):
            self.alice = User.objects.get(username="alice")
            self.bob = User.objects.get(username="bob")
            self.welcome = Note.objects.get(title="Welcome to Angee")

    def test_note_is_a_rebac_resource(self) -> None:
        self.assertEqual(model_resource_type(Note), "notes/note")

    def test_demo_load_grants_each_creator_ownership(self) -> None:
        # created_by drives the owner grant: a user reaches only their notes.
        alice_notes = list(Note.objects.as_user(self.alice))
        bob_notes = list(Note.objects.as_user(self.bob))

        self.assertTrue(alice_notes)
        self.assertTrue(bob_notes)
        self.assertTrue(
            all(note.created_by_id == self.alice.pk for note in alice_notes)
        )
        self.assertTrue(
            all(note.created_by_id == self.bob.pk for note in bob_notes)
        )
        self.assertFalse(
            {note.sqid for note in alice_notes}
            & {note.sqid for note in bob_notes}
        )

    def test_demo_load_is_idempotent(self) -> None:
        with system_context(reason="test"):
            notes_before = Note.objects.count()
            users_before = User.objects.count()
        call_command(
            "resources", "load", "demo", allow_non_dev=True, verbosity=0
        )
        with system_context(reason="test"):
            self.assertEqual(Note.objects.count(), notes_before)
            self.assertEqual(User.objects.count(), users_before)

    def test_reader_grant_redacts_owner_only_fields(self) -> None:
        write_relationships(
            [
                RelationshipTuple(
                    resource=ObjectRef("notes/note", self.welcome.sqid),
                    relation="reader",
                    subject=SubjectRef.of("auth/user", str(self.bob.pk)),
                )
            ]
        )
        sqid = self.welcome.sqid
        bob_view = Note.objects.as_user(self.bob).get(sqid=sqid)
        owner_view = Note.objects.as_user(self.alice).get(sqid=sqid)

        self.assertEqual(bob_view.title, "Welcome to Angee")
        self.assertIsNone(bob_view.is_starred)  # read__is_starred = owner
        self.assertTrue(owner_view.is_starred)

    def test_deleting_a_note_clears_its_relationships(self) -> None:
        sqid = self.welcome.sqid
        with system_context(reason="test"):
            Note.objects.get(sqid=sqid).delete()
        # The owner can no longer reach the note: its grants were cleared.
        self.assertEqual(
            list(Note.objects.as_user(self.alice).filter(sqid=sqid)), []
        )
