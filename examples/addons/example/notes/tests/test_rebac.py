"""Authorization behaviour of the composed notes addon.

These exercise the real runtime model against the permission schema: demo
resources seed users and notes, field-backed ownership lets creators read their
rows, and field permissions redact owner-only fields from other readers.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import TransactionTestCase, override_settings
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    system_context,
    write_relationships,
)
from rebac.backends import backend
from rebac.models import active_relationship_model
from rebac.resources import model_resource_type

Note = apps.get_model("notes", "Note")
User = get_user_model()


class NotesAuthorizationTests(TransactionTestCase):
    """Drive the demo resources through the permission schema."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.admin = User.objects.get(username="admin")
            self.alice = User.objects.get(username="alice")
            self.bob = User.objects.get(username="bob")
            self.welcome = Note.objects.get(title="Welcome to Angee")

    def test_note_is_a_rebac_resource(self) -> None:
        self.assertEqual(model_resource_type(Note), "notes/note")

    def test_scoped_for_aggregate_is_actor_scoped_and_fails_closed(self) -> None:
        # Aggregates compile through .values()/.aggregate() (no field redaction),
        # so scope must be applied to rows: per-actor scoping, unscoped only under
        # an explicit sudo, and empty (never the full table) with no actor.
        with system_context(reason="test"):
            total = Note.objects.count()
        alice_total = len(list(Note.objects.as_user(self.alice)))
        self.assertLess(alice_total, total)

        # An actor's aggregate sees only that actor's rows.
        self.assertEqual(Note.objects.as_user(self.alice).scoped_for_aggregate().count(), alice_total)

        # Ambient system_context and per-queryset .sudo() both aggregate across all rows.
        with system_context(reason="test"):
            self.assertEqual(Note.objects.all().scoped_for_aggregate().count(), total)
        self.assertEqual(Note.objects.all().sudo(reason="report").scoped_for_aggregate().count(), total)

        # The leak scenario: no actor with REBAC_STRICT_MODE off must fail closed
        # (empty), never the full table. Strict mode already raises (also closed).
        with override_settings(REBAC_STRICT_MODE=False):
            self.assertEqual(Note.objects.all().scoped_for_aggregate().count(), 0)

    def test_demo_load_uses_created_by_field_backed_ownership(self) -> None:
        # created_by drives the owner relation: a user reaches only their notes.
        alice_notes = list(Note.objects.as_user(self.alice))
        bob_notes = list(Note.objects.as_user(self.bob))

        self.assertTrue(alice_notes)
        self.assertTrue(bob_notes)
        self.assertTrue(all(note.created_by_id == self.alice.pk for note in alice_notes))
        self.assertTrue(all(note.created_by_id == self.bob.pk for note in bob_notes))
        self.assertFalse({note.sqid for note in alice_notes} & {note.sqid for note in bob_notes})
        relationship_model = active_relationship_model()
        self.assertFalse(
            relationship_model.objects.filter(
                resource_type="notes/note",
                relation="owner",
            ).exists()
        )
        definition = backend().schema().get_definition("notes/note")
        if definition is None:
            self.fail("notes/note must be present in the synced REBAC schema")
        owner = next(relation for relation in definition.relations if relation.name == "owner")
        backing = owner.backing
        if backing is None:
            self.fail("notes/note#owner must be field-backed by created_by")
        self.assertEqual(backing.attname, "created_by")

    def test_platform_admin_reaches_all_notes(self) -> None:
        with system_context(reason="test"):
            total = Note.objects.count()
        relationship_model = active_relationship_model()

        # as_user(admin) is NOT bracketed in sudo, so reach is purely
        # relationship-based: the const-backed admin relation points every note
        # at angee/role:admin, and admin->member resolves the superuser's
        # membership (mirrored by the IAM user save hook).
        admin_notes = Note.objects.as_user(self.admin)

        self.assertEqual(admin_notes.count(), total)
        self.assertGreaterEqual(
            {note.created_by_id for note in admin_notes},
            {self.admin.pk, self.alice.pk, self.bob.pk},
        )
        # One role membership powers it — not a grant per note.
        self.assertTrue(
            relationship_model.objects.filter(
                resource_type="angee/role",
                resource_id="admin",
                relation="member",
            ).exists()
        )
        # The admin relation is synthetic (const-backed): the note table holds
        # no REBAC tuples at all.
        self.assertFalse(relationship_model.objects.filter(resource_type="notes/note").exists())
        admin_rel = next(
            relation
            for relation in backend().schema().get_definition("notes/note").relations
            if relation.name == "admin"
        )
        self.assertEqual(admin_rel.backing.kind, "const")
        self.assertEqual(admin_rel.backing.target_id, "admin")

    def test_demo_load_is_idempotent(self) -> None:
        with system_context(reason="test"):
            notes_before = Note.objects.count()
            users_before = User.objects.count()
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test"):
            self.assertEqual(Note.objects.count(), notes_before)
            self.assertEqual(User.objects.count(), users_before)

    def test_demo_users_authenticate(self) -> None:
        alice = authenticate(username="alice", password="alice")
        bob = authenticate(username="bob", password="bob")

        self.assertIsNotNone(alice)
        self.assertIsNotNone(bob)
        self.assertTrue(alice.is_staff)
        self.assertFalse(bob.is_staff)

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
        relationship_model = active_relationship_model()
        with system_context(reason="test"):
            Note.objects.get(sqid=sqid).delete()
        # Owner reach is field-backed and admin reach const-backed, so a note
        # carries no tuples; deleting the row removes it and leaves no orphans.
        self.assertEqual(list(Note.objects.as_user(self.alice).filter(sqid=sqid)), [])
        self.assertFalse(
            relationship_model.objects.filter(
                resource_type="notes/note", resource_id=str(sqid)
            ).exists()
        )
