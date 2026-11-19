"""Parties' roles: a contact reader, manager and admin hold on every party what a per-record grant holds on one.

The decision table runs every actor against one linked person, one person nobody
shared, the person's own handle, the directory and the people vault. A
directory's contact readers keep exactly what they read before roles existed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rebac import PermissionDenied, actor_context, system_context
from rebac.roles import grant as grant_role

from angee.messaging.testing.models import Circle, CircleMember, Handle, Party, Person
from angee.resources.testing.models import Resource
from tests.conftest import MarkdownPage, Page, RecordBinding, Vault
from tests.test_parties_directory_readers import _allowed, _grant, _scoped
from tests.test_parties_directory_readers import contacts as contacts  # noqa: F401 -- the shared contacts world

ROLES = ("contact_reader", "contact_manager", "contact_admin")


@pytest.fixture
def world(contacts: SimpleNamespace) -> SimpleNamespace:
    """The contacts world, a holder of each role, a per-record editor, a user with no role and the people vault."""

    users = get_user_model()
    c = contacts
    holders = {role: users.objects.create_user(username=f"role-{role}") for role in ROLES}
    editor = users.objects.create_user(username="role-per-record-editor")
    nobody = users.objects.create_user(username="role-nobody")
    with system_context(reason="tests.parties_roles.seed"):
        for role, user in holders.items():
            grant_role(actor=user, role=f"parties/role:{role}")
        _grant(c.person, "editor", editor)
        _grant(c.person_party, "editor", editor)
    Resource.objects.load_addons((apps.get_app_config("knowledge"),), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties_roles.vault"):
        vault = Vault.objects.for_resource(Vault.PEOPLE)
        folder = RecordBinding.objects.party_folder(c.unlinked, vault=vault, title="Grace Unlinked (folder)")
        note = Page.objects.create_in(vault, parent=folder, title="Grace Unlinked: notes", body="Likes jazz.")
    return SimpleNamespace(
        **vars(c), holders=holders, editor=editor, nobody=nobody, vault=vault, folder=folder, note=note
    )


def _actors(w: SimpleNamespace) -> dict[str, Any]:
    return {
        "no role": w.nobody,
        "contact reader": w.holders["contact_reader"],
        "contact manager": w.holders["contact_manager"],
        "contact admin": w.holders["contact_admin"],
        "owner": w.owner,
        "per-record editor": w.editor,
        "directory contact reader": w.reader,
        "platform admin": w.admin,
    }


# Each row: what the actor holds on the linked person, in this column order.
COLUMNS = ("read", "read_private", "write", "trash", "transfer", "vault_read", "vault_write")
TABLE = {
    "no role": (False, False, False, False, False, False, False),
    # Choice 1 (yes): a contact reader reads the private fields, as a per-record reader does.
    "contact reader": (True, True, False, False, False, True, False),
    # A merge needs write on both parties (Party.MERGED_PERMISSION), so a contact manager merges; trash is
    # the row's ``delete`` and stays with the contact admin (see the report on choice 2).
    "contact manager": (True, True, True, False, False, True, True),
    "contact admin": (True, True, True, True, True, True, True),
    "owner": (True, True, True, True, True, False, False),
    "per-record editor": (True, True, True, False, False, False, False),
    "directory contact reader": (True, False, False, False, False, False, False),
    "platform admin": (True, True, True, True, True, True, True),
}


def _holds(w: SimpleNamespace, actor: Any) -> tuple[bool, ...]:
    return (
        _allowed(w.person, actor, "read"),
        _allowed(w.person, actor, "read_private"),
        _allowed(w.person, actor, "write"),
        _allowed(w.person_party, actor, "delete"),
        _allowed(w.person_party, actor, "transfer"),
        _allowed(w.vault, actor, "read"),
        _allowed(w.vault, actor, "write"),
    )


def test_the_decision_table_per_actor(world: SimpleNamespace) -> None:
    w = world
    for label, actor in _actors(w).items():
        assert dict(zip(COLUMNS, _holds(w, actor), strict=True)) == dict(zip(COLUMNS, TABLE[label], strict=True)), label


def test_a_role_holder_sees_a_person_nobody_shared_with_them_and_their_pages(world: SimpleNamespace) -> None:
    """The unlinked person: no grant, no directory; each role reads them, their folder and their note."""

    w = world
    for role, actor in w.holders.items():
        assert Person.objects.with_actor(actor).filter(pk=w.unlinked.pk).exists(), role
        assert _scoped(w.unlinked, actor), role
        assert Page.objects.with_actor(actor).filter(pk__in=[w.folder.pk, w.note.pk]).count() == 2, role
        assert Vault.objects.with_actor(actor).filter(pk=w.vault.pk).exists(), role
    for actor in (w.nobody, w.reader, w.editor):
        assert not Person.objects.with_actor(actor).filter(pk=w.unlinked.pk).exists(), actor.username
        assert not Vault.objects.with_actor(actor).filter(pk=w.vault.pk).exists(), actor.username
    with actor_context(w.holders["contact_manager"]):
        MarkdownPage.objects.write_body(w.note, "Likes jazz and tea.")
    with actor_context(w.holders["contact_reader"]), pytest.raises(PermissionDenied):
        MarkdownPage.objects.write_body(w.note, "Likes nothing.")


def test_roles_reach_handles_addresses_relationships_and_the_review_verbs(world: SimpleNamespace) -> None:
    """A reader reads, a manager writes (confirms, corrects, merges), an admin also deletes; nobody else."""

    w = world
    reader, manager, admin = (w.holders[role] for role in ROLES)
    for row in (w.own_handle, w.address, w.edge):
        assert _allowed(row, reader) and not _allowed(row, reader, "write"), type(row).__name__
        assert _allowed(row, manager, "write") and not _allowed(row, manager, "delete"), type(row).__name__
        assert _allowed(row, admin, "delete"), type(row).__name__
        assert not _allowed(row, w.nobody), type(row).__name__
    assert _allowed(w.person, manager, "adopt") and not _allowed(w.person, reader, "adopt")
    # Adoption never runs through the platform admin's reach of every role.
    assert not _allowed(w.person, w.admin, "adopt")

    with system_context(reason="tests.parties_roles.merge_seed"):
        survivor = Person.objects.create(display_name="Ada Example", created_by=w.owner)
        merged = Person.objects.create(display_name="Ada E.", created_by=w.owner)
        apart = Person.objects.create(display_name="Ada Other", created_by=w.owner)
    # Keeping two people separate is a statement on their pair: a manager makes it, a reader cannot.
    with actor_context(reader), pytest.raises(PermissionDenied):
        Party.same_as.for_pair(survivor, apart).with_actor(reader).dismiss_statement(aspect="same_as")
    with actor_context(manager):
        Party.same_as.for_pair(survivor, apart).with_actor(manager).dismiss_statement(aspect="same_as")
        # Confirming the person's name is a statement on the party, admitted by its write.
        Person.objects.get(pk=survivor.pk).confirm_statement(aspect="name", values={"given_name": "Ada"})
    with system_context(reason="tests.parties_roles.kept_separate"):
        assert Party.same_as.kept_separate(survivor, apart)
    with actor_context(manager):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])
    with system_context(reason="tests.parties_roles.merge_readback"):
        assert Party._base_manager.get(pk=merged.pk).merged_into_id == survivor.pk


def test_a_partyless_handle_is_out_of_every_roles_reach(world: SimpleNamespace) -> None:
    """A mailbox's sender or a calendar's attendee, no party's yet: the roles reach a handle through its party."""

    w = world
    with system_context(reason="tests.parties_roles.partyless"):
        handle = Handle.objects.create(platform="email", value="sender@example.com", created_by=w.outsider)
        member = CircleMember.objects.create(circle=Circle.objects.create(name="Close"), party=w.unlinked)
    assert handle.party_id is None
    for role, actor in w.holders.items():
        for action in ("read", "write", "delete"):
            assert not _allowed(handle, actor, action), (role, action)
        assert not Handle.objects.with_actor(actor).filter(party__isnull=True).exists(), role
    assert _allowed(handle, w.outsider)
    # A circle membership names its member: the roles reach it through that party.
    assert _allowed(member, w.holders["contact_reader"]) and not _allowed(member, w.holders["contact_reader"], "write")
    assert _allowed(member, w.holders["contact_manager"], "write")
    assert _allowed(member, w.holders["contact_admin"], "delete")


def test_a_contact_admin_reads_directories_and_shares_their_contacts_but_does_not_connect_or_change_them(
    world: SimpleNamespace,
) -> None:
    w = world
    admin = w.holders["contact_admin"]
    assert _allowed(w.directory, admin, "read") and _allowed(w.directory, admin, "share")
    for action in ("write", "delete", "create"):
        assert not _allowed(w.directory, admin, action), action
    for role in ("contact_reader", "contact_manager"):
        assert not _allowed(w.directory, w.holders[role], "read"), role
    # The integration's own owner and the platform admin still share it.
    assert _allowed(w.directory, w.owner, "share") and _allowed(w.directory, w.admin, "share")
