"""A person's folder in the people vault: provisioned once, made once per person, read through the vault."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, actor_context, system_context

from angee.messaging.testing.models import Party, Person
from angee.resources.testing.models import Resource
from tests.conftest import MarkdownPage, Page, RecordBinding, Vault, create_platform_admin, create_user
from tests.test_knowledge import _grant


def _people_vault() -> Any:
    """Load knowledge's install tier, as provisioning does, and resolve the people vault by its handle."""

    Resource.objects.load_addons((apps.get_app_config("knowledge"),), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.knowledge.people_vault"):
        return Vault.objects.for_resource(Vault.PEOPLE)


def _person(owner: Any, name: str = "Ada Example") -> Any:
    with actor_context(owner):
        return Person.objects.create(display_name=name)


def _folders(person: Any) -> list[Any]:
    with system_context(reason="tests.knowledge.folders"):
        bindings = RecordBinding.objects.for_record(person, role=RecordBinding.objects.FOLDER_ROLE).order_by("page_id")
        return [binding.page_id for binding in bindings]


def test_the_people_vault_is_provisioned_once_unowned_and_administered_by_platform_admins(db: None) -> None:
    """Its readers are the vault's own: nobody until granted, but its administrators."""

    vault = _people_vault()
    assert (vault.name, vault.owner_id) == ("People", None)
    replay = Resource.objects.load_addons((apps.get_app_config("knowledge"),), tiers=[Resource.Tier.INSTALL])
    assert (replay.created, replay.updated) == (0, 0)

    admin, outsider = create_platform_admin("people-vault-admin"), create_user("people-vault-outsider")
    assert Vault.objects.with_actor(admin).filter(pk=vault.pk).exists()
    assert vault.as_user(admin).has_access("share") and vault.as_user(admin).has_access("transfer")
    assert not Vault.objects.with_actor(outsider).filter(pk=vault.pk).exists()


def test_a_vault_viewer_reads_a_persons_folder_and_note_and_a_reader_of_the_person_alone_does_not(
    db: None,
) -> None:
    """Pages read through the vault: a binding to the person lends nothing, and an editor writes."""

    vault = _people_vault()
    owner, viewer, editor, person_reader = (
        create_user(f"people-pages-{name}") for name in ("owner", "viewer", "editor", "person-reader")
    )
    person = _person(owner)
    with system_context(reason="tests.knowledge.import"):
        folder = RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)")
        subpage = Page.objects.create_in(vault, parent=folder, title="Ada Example: notes")
        party = Party.objects.get(pk=person.pk)
    _grant(vault, "viewer", viewer)
    _grant(vault, "editor", editor)
    _grant(party, "identity_reader", person_reader)

    assert Person.objects.with_actor(person_reader).filter(pk=person.pk).exists()
    for actor, readable in ((viewer, True), (editor, True), (person_reader, False), (owner, False)):
        visible = Page.objects.with_actor(actor).filter(pk__in=[folder.pk, subpage.pk]).count()
        assert visible == (2 if readable else 0), actor.username

    with actor_context(editor):
        MarkdownPage.objects.write_body(subpage, "Prefers tea.")
    with actor_context(viewer), pytest.raises(PermissionDenied):
        MarkdownPage.objects.write_body(subpage, "Prefers coffee.")
    with system_context(reason="tests.knowledge.people_pages.readback"):
        assert MarkdownPage.objects.get(pk=subpage.pk).body == "Prefers tea."


def test_a_persons_folder_is_made_once_and_found_after_by_a_person_or_an_import(db: None) -> None:
    """A person makes it by the vault's own permissions and the binding's; an import as system work."""

    vault = _people_vault()
    owner, editor = create_user("people-folder-owner"), create_user("people-folder-editor")
    person = _person(owner)
    _grant(vault, "editor", editor)

    # Writing the vault is not writing the person: the binding needs both, so nothing is made.
    with actor_context(editor), pytest.raises(PermissionDenied):
        RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)")
    assert _folders(person) == []

    _grant(vault, "editor", owner)
    with actor_context(owner):
        folder = RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)")
        assert RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)").pk == folder.pk
        assert folder.kind == Page.PageKind.FOLDER
    with system_context(reason="tests.knowledge.import"):
        assert RecordBinding.objects.party_folder(person, vault=vault, title="Ada Example (folder)").pk == folder.pk
    assert _folders(person) == [folder.pk]


def test_after_a_merge_the_survivor_keeps_both_folders_and_its_folder_is_the_oldest(db: None) -> None:
    """Nothing is lost and no third folder is made; a person tidies the second when they choose."""

    vault = _people_vault()
    owner = create_user("people-merge-owner")
    survivor, merged = _person(owner, "Ada Example"), _person(owner, "Ada E.")
    with system_context(reason="tests.knowledge.import"):
        older = RecordBinding.objects.party_folder(merged, vault=vault, title="Ada E. (folder)")
        newer = RecordBinding.objects.party_folder(survivor, vault=vault, title="Ada Example (folder)")
        kept = Page.objects.create_in(vault, parent=older, title="Ada E.: notes")
    with actor_context(owner):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])

    assert _folders(survivor) == [older.pk, newer.pk]
    with system_context(reason="tests.knowledge.import"):
        assert RecordBinding.objects.party_folder(survivor, vault=vault, title="Another title").pk == older.pk
        assert Page.objects.get(pk=kept.pk).parent_id == older.pk
    assert _folders(survivor) == [older.pk, newer.pk]


def _write(person: Any, vault: Any, body: str, written: str | None, *, title: str = "Ada Example: remember") -> str:
    """Write the ``remember`` text as an import does."""

    with system_context(reason="tests.knowledge.import"):
        return RecordBinding.objects.write_sourced_page(
            person, vault=vault, role="remember", folder_title=f"{person.display_name} (folder)", title=title,
            body=body, written=written,
        )


def _notes(person: Any) -> list[Any]:
    """The person's live pages bound under ``remember``, oldest first."""

    with system_context(reason="tests.knowledge.sourced.notes"):
        bindings = RecordBinding.objects.for_record(person, role="remember").filter(page__is_trashed=False)
        return list(MarkdownPage.objects.filter(pk__in=bindings.values("page_id")).order_by("pk"))


def _note(person: Any) -> Any:
    notes = _notes(person)
    return notes[0] if notes else None


def test_a_sourced_note_is_written_once_updated_while_untouched_and_never_written_over_an_edit(db: None) -> None:
    """The four cases: a new text, the same text, a changed text on an untouched note, and on an edited one."""

    vault = _people_vault()
    owner, editor = create_user("people-sourced-owner"), create_user("people-sourced-editor")
    person = _person(owner)
    _grant(vault, "editor", editor)

    def write(body: str, written: str | None) -> str:
        return _write(person, vault, body, written)

    def note() -> Any:
        return _note(person)

    # An empty first text writes nothing at all, not even the folder.
    assert write("", None) == "unchanged"
    assert note() is None and _folders(person) == []

    assert write("Prefers tea.", None) == "written"
    written = note()
    assert written.body == "Prefers tea." and written.parent_id == _folders(person)[0]
    revisions = MarkdownPage.history.filter(id=written.pk).count()

    # The same text again writes nothing.
    assert write("Prefers tea.", "Prefers tea.") == "unchanged"
    assert MarkdownPage.history.filter(id=written.pk).count() == revisions

    # A changed text updates a note nobody edited since the source wrote it.
    assert write("Prefers green tea.", "Prefers tea.") == "written"
    assert note().body == "Prefers green tea."

    # A person's edit is never written over: the newer text lands below it, marked, for them to take or leave.
    with actor_context(editor):
        MarkdownPage.objects.write_body(note(), "Prefers green tea. Ask about the garden.")
    assert write("Prefers coffee.\nNo sugar.", "Prefers green tea.") == "edited"
    body = note().body
    assert body.startswith("Prefers green tea. Ask about the garden.")
    assert "> Prefers coffee.\n> No sugar." in body
    assert write("Prefers coffee.\nNo sugar.", "Prefers coffee.\nNo sugar.") == "unchanged"
    assert note().body == body


def test_a_renamed_or_moved_note_stays_the_sources_note(db: None) -> None:
    """The note is found by its binding to the person under the caller's role, never by its title or place."""

    vault = _people_vault()
    owner, editor = create_user("people-renamed-owner"), create_user("people-renamed-editor")
    person = _person(owner)
    _grant(vault, "editor", editor)
    assert _write(person, vault, "Prefers tea.", None) == "written"
    note = _note(person)

    with actor_context(editor):
        renamed = Page.objects.get(pk=note.pk)
        renamed.title = "What to remember about Ada"
        renamed.save(update_fields=["title", "updated_at"])
    assert _write(person, vault, "Prefers green tea.", "Prefers tea.") == "written"
    assert [row.pk for row in _notes(person)] == [note.pk]
    assert (_note(person).title, _note(person).body) == ("What to remember about Ada", "Prefers green tea.")

    with actor_context(editor):
        elsewhere = Page.objects.create_in(vault, title="Kept notes", kind=Page.PageKind.FOLDER)
        moved = Page.objects.get(pk=note.pk)
        moved.parent = elsewhere
        moved.save(update_fields=["parent", "updated_at"])
    assert _write(person, vault, "Prefers coffee.", "Prefers green tea.") == "written"
    assert [row.pk for row in _notes(person)] == [note.pk]
    assert (_note(person).parent_id, _note(person).body) == (elsewhere.pk, "Prefers coffee.")


def test_after_a_merge_the_oldest_note_under_a_role_is_the_sources_and_none_is_made(db: None) -> None:
    vault = _people_vault()
    owner = create_user("people-merged-notes-owner")
    survivor, merged = _person(owner, "Ada Example"), _person(owner, "Ada E.")
    assert _write(merged, vault, "Met at the door.", None, title="Ada E.: remember") == "written"
    assert _write(survivor, vault, "Prefers tea.", None, title="Ada Example: remember") == "written"
    older, newer = _notes(merged)[0], _notes(survivor)[0]
    with actor_context(owner):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])

    assert [row.pk for row in _notes(survivor)] == [older.pk, newer.pk]
    assert _write(survivor, vault, "Met at the door, twice.", "Met at the door.") == "written"
    assert [row.pk for row in _notes(survivor)] == [older.pk, newer.pk]
    with system_context(reason="tests.knowledge.sourced.readback"):
        assert MarkdownPage.objects.get(pk=older.pk).body == "Met at the door, twice."
        assert MarkdownPage.objects.get(pk=newer.pk).body == "Prefers tea."


def test_a_sourced_notes_role_is_a_slug_of_its_own_and_its_new_title_must_be_free(db: None) -> None:
    """The model's own roles are refused; a title another page holds refuses the new note, saying so."""

    vault = _people_vault()
    owner = create_user("people-roles-owner")
    person = _person(owner)
    with system_context(reason="tests.knowledge.import"):
        for role in ("folder", " related ", "two words"):
            with pytest.raises(ValidationError):
                RecordBinding.objects.write_sourced_page(
                    person, vault=vault, role=role, folder_title="Ada Example (folder)", title="Ada Example: x",
                    body="Text", written=None,
                )
        Page.objects.create_in(vault, title="Ada Example: remember")
    with pytest.raises(ValidationError, match="already uses the title"):
        _write(person, vault, "Prefers tea.", None)
    assert _notes(person) == []
    assert _write(person, vault, "Prefers tea.", None, title="Ada Example: remember (2)") == "written"
