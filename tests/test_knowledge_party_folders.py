"""A person's folder in the people vault: provisioned once, made once per person, read through the vault."""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps
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


def test_a_vault_viewer_reads_a_persons_folder_and_subpage_and_a_reader_of_the_person_alone_does_not(
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
        subpage = Page.objects.subpage(folder, title="Ada Example: notes")
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
        subpage = Page.objects.subpage(folder, title="Ada Example: notes")
        assert Page.objects.subpage(folder, title="Ada Example: notes").pk == subpage.pk
        assert (subpage.parent_id, subpage.kind) == (folder.pk, Page.PageKind.NOTE)
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
        kept = Page.objects.subpage(older, title="Ada E.: notes")
    with actor_context(owner):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])

    assert _folders(survivor) == [older.pk, newer.pk]
    with system_context(reason="tests.knowledge.import"):
        assert RecordBinding.objects.party_folder(survivor, vault=vault, title="Another title").pk == older.pk
        assert Page.objects.get(pk=kept.pk).parent_id == older.pk
    assert _folders(survivor) == [older.pk, newer.pk]
