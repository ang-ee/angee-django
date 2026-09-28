"""Native notes adoption cases loaded only by the shared composed host."""

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import actor_context, system_context
from rebac.errors import PermissionDenied
from rebac.models import active_relationship_model


class NotesOwnershipTests(TransactionTestCase):
    def setUp(self):
        call_command("rebac", "sync", verbosity=0)
        self.note_model = apps.get_model("notes", "Note")
        self.author = get_user_model().objects.create_user(username="note-author")
        self.recipient = get_user_model().objects.create_user(username="note-recipient")

    def test_transfer_changes_readers_without_rewriting_attribution_or_body(self):
        with actor_context(self.author):
            note = self.note_model.objects.create(title="Transfer", body="Retained body")
            note.transfer_ownership(self.recipient)
            self.assertFalse(self.note_model.objects.filter(pk=note.pk).exists())
        with actor_context(self.recipient):
            received = self.note_model.objects.get(pk=note.pk)
            self.assertEqual(received.created_by_id, self.author.pk)
            self.assertEqual(received.owner_id, self.recipient.pk)
            self.assertEqual(received.body, "Retained body")
            self.assertEqual(received.word_count, 2)
            received.transfer_ownership(None)
            self.assertFalse(self.note_model.objects.filter(pk=note.pk).exists())
        with system_context(reason="test.notes.ownership.inspect"):
            persisted = self.note_model.objects.get(pk=note.pk)
            self.assertIsNone(persisted.owner_id)
            self.assertEqual(persisted.created_by_id, self.author.pk)
            self.assertFalse(active_relationship_model().objects.filter(
                resource_type="notes/note", resource_id=str(note.pk), relation="owner",
            ).exists())

    def test_attribution_edit_does_not_transfer_read_or_transfer_permission(self):
        with actor_context(self.author):
            note = self.note_model.objects.create(title="Attribution")
            note.created_by = self.recipient
            note.save(update_fields=["created_by"])
            self.assertTrue(self.note_model.objects.filter(pk=note.pk).exists())
        with actor_context(self.recipient):
            self.assertFalse(self.note_model.objects.filter(pk=note.pk).exists())
            with self.assertRaises(PermissionDenied):
                note.with_actor(self.recipient).transfer_ownership(self.recipient)
