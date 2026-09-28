"""The example adopter uses the same raw owner gate as framework records."""

from django.test import TransactionTestCase, override_settings
from rebac import PermissionDenied, actor_context, system_context

from tests.notes_ownership_cases import NotesOwnershipTests


class NotesOwnerCampaign(TransactionTestCase):
    storage = "registry"

    def setUp(self):
        setting = override_settings(REBAC_LOCAL_BACKEND_STORAGE=self.storage, REBAC_SUPERUSER_BYPASS=False)
        setting.enable()
        self.addCleanup(setting.disable)
        NotesOwnershipTests.setUp(self)
        with system_context(reason="tests.t3.note_identities"):
            for role in ("author", "recipient"):
                user = getattr(self, role)
                user.username = f"{self._testMethodName}-{role}"
                user.email = f"{self._testMethodName}-{role}@example.test"
                user.save(update_fields=("username", "email"))

    def test_editor_cannot_write_owner_by_save_or_queryset_but_can_edit_body(self):
        with actor_context(self.author):
            note = self.note_model.objects.create(title="Owner gate", body="Original")
            note.grant_record_access("editor", self.recipient)
        with actor_context(self.recipient):
            note = self.note_model.objects.get(pk=note.pk)
            note.body = "Edited"
            note.save(update_fields=("body",))
            for field in ("owner", "owner_id"):
                value = self.recipient if field == "owner" else self.recipient.pk
                for path in ("full", "partial", "queryset"):
                    with self.subTest(field=field, path=path), self.assertRaises(PermissionDenied):
                        candidate = self.note_model.objects.get(pk=note.pk)
                        setattr(candidate, field, value)
                        if path == "queryset":
                            self.note_model.objects.filter(pk=note.pk).update(**{field: value})
                        else:
                            candidate.save(**({"update_fields": (field,)} if path == "partial" else {}))
        persisted = self.note_model._base_manager.get(pk=note.pk)
        self.assertEqual(
            (persisted.owner_id, persisted.created_by_id, persisted.body),
            (
                self.author.pk,
                self.author.pk,
                "Edited",
            ),
        )


class NotesOwnerDenormalizedCampaign(NotesOwnerCampaign):
    storage = "denormalized"
