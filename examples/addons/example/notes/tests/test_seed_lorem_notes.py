"""The seed_lorem_notes command: owned, backdated, scope-visible notes."""

from __future__ import annotations

from datetime import datetime, timezone

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase
from rebac import system_context

from angee.compose.dependencies import AddonDependencyGroup

Note = apps.get_model("notes", "Note")
User = get_user_model()


class SeedLoremNotesDependencyTests(SimpleTestCase):
    """The composed host installs the dependency needed to import this command."""

    def test_notes_projects_faker_into_runtime_dependencies(self) -> None:
        group = AddonDependencyGroup.from_app_configs((apps.get_app_config("notes"),), project_dir=None)

        self.assertIn("faker>=30.0", group.compile())


class SeedLoremNotesTests(TransactionTestCase):
    """Drive the command against the composed runtime + permission schema."""

    def setUp(self) -> None:
        call_command("rebac", "sync", verbosity=0)
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="test-setup"):
            self.alice = User.objects.get(username="alice")
            self.bob = User.objects.get(username="bob")

    def _seed(self, **overrides: object) -> None:
        call_command(
            "seed_lorem_notes",
            count=overrides.get("count", 50),
            owner=overrides.get("owner", "alice"),
            start=overrides.get("start", "2021-01-01"),
            end=overrides.get("end", "2023-12-31"),
            seed=overrides.get("seed", 7),
            batch=overrides.get("batch", 20),
            fresh=overrides.get("fresh", False),
            verbosity=0,
        )

    def test_seeded_notes_are_visible_to_owner_and_dated_in_range(
        self,
    ) -> None:
        self._seed(count=50, owner="alice", fresh=True)

        # Visible to alice via REBAC scope — not merely present in the table.
        alice_notes = list(Note.objects.as_user(self.alice))
        self.assertGreaterEqual(len(alice_notes), 50)
        self.assertTrue(all(n.created_by_id == self.alice.pk for n in alice_notes))

        # created_at landed inside the requested window.
        low = datetime(2021, 1, 1, tzinfo=timezone.utc)
        high = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self.assertTrue(all(low <= n.created_at < high for n in alice_notes))

    def test_seeded_notes_store_word_count(self) -> None:
        self._seed(count=5, owner="alice", fresh=True, seed=42, batch=2)

        with system_context(reason="test"):
            notes = list(Note.objects.filter(created_by=self.alice))

        self.assertEqual(len(notes), 5)
        self.assertTrue(any(note.word_count > 0 for note in notes))
        self.assertTrue(all(note.word_count == Note.count_words(note.body) for note in notes))

    def test_owner_isolation(self) -> None:
        self._seed(count=30, owner="alice", fresh=True)
        alice_sqids = {n.sqid for n in Note.objects.as_user(self.alice)}
        bob_sqids = {n.sqid for n in Note.objects.as_user(self.bob)}
        self.assertGreaterEqual(len(alice_sqids), 30)
        self.assertFalse(alice_sqids & bob_sqids)

    def test_seed_is_deterministic(self) -> None:
        self._seed(count=12, owner="alice", seed=99, fresh=True)
        with system_context(reason="test"):
            first = sorted(Note.objects.filter(created_by=self.alice).values_list("title", flat=True))
        self._seed(count=12, owner="alice", seed=99, fresh=True)
        with system_context(reason="test"):
            second = sorted(Note.objects.filter(created_by=self.alice).values_list("title", flat=True))
        self.assertEqual(first, second)

    def test_unknown_owner_fails_fast(self) -> None:
        with self.assertRaises(CommandError):
            self._seed(count=1, owner="nobody")

    def test_nonpositive_batch_fails_before_fresh_deletes(self) -> None:
        with system_context(reason="test-seed-validation"):
            before = set(Note.objects.values_list("pk", flat=True))

        for batch in (0, -1):
            with self.subTest(batch=batch):
                with self.assertRaisesMessage(CommandError, "--batch must be positive"):
                    self._seed(count=0, batch=batch, fresh=True)
                with system_context(reason="test-seed-validation"):
                    self.assertEqual(set(Note.objects.values_list("pk", flat=True)), before)
