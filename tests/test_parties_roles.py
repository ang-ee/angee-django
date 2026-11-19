"""Parties' roles: a contact reader, manager and admin hold on every party what a per-record grant holds on one.

The decision table runs every kind of actor (each role, a manager through a group,
the owner, a per-record editor, a directory's contact reader, a platform admin and
nobody) against every row the roles reach: a person and their party, an
organization, a handle, an address, a relationship, a folder, a circle and its
membership, the people vault and a page in it, a record task about the party and
the directory. A directory's contact readers keep exactly what they read before
roles existed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from rebac import PermissionDenied, SubjectRef, actor_context, system_context, to_object_ref
from rebac.roles import grant as grant_role

from angee.messaging.testing.models import Circle, CircleMember, Directory, Folder, Handle, Party, Person
from angee.projects.testing.models import Task
from angee.resources.testing.models import Resource
from tests.conftest import MarkdownPage, Page, RecordBinding, Vault
from tests.iam_models import Group
from tests.test_parties_directory_readers import _allowed, _grant, _scoped
from tests.test_parties_directory_readers import contacts as contacts  # noqa: F401 -- the shared contacts world

ROLES = ("contact_reader", "contact_manager", "contact_admin")


@pytest.fixture
def world(contacts: SimpleNamespace) -> SimpleNamespace:
    """The contacts world, a holder of each role (and a manager through a group), a per-record editor, a user
    with no role, the people vault, an address-book folder, a circle with a membership and a record task."""

    users = get_user_model()
    c = contacts
    holders = {role: users.objects.create_user(username=f"role-{role}") for role in ROLES}
    editor = users.objects.create_user(username="role-per-record-editor")
    nobody = users.objects.create_user(username="role-nobody")
    group_manager = users.objects.create_user(username="role-group-manager")
    with system_context(reason="tests.parties_roles.seed"):
        for role, user in holders.items():
            grant_role(actor=user, role=f"parties/role:{role}")
        _grant(c.person, "editor", editor)
        _grant(c.person_party, "editor", editor)
        group = Group.objects.create(name="Contact managers")
        group.add_member(str(SubjectRef(to_object_ref(group_manager))))
        grant_role(actor=SubjectRef.of("auth/group", str(group.pk), "member"), role="parties/role:contact_manager")
        book = Folder.objects.create(name="Book", owner=c.owner, created_by=c.owner)
        circle = Circle.objects.create(name="Close", owner=c.owner, created_by=c.owner)
        membership = CircleMember.objects.create(circle=circle, party=c.person_party, created_by=c.owner)
        task = Task.objects.create(subject=c.person_party, title="About Ada", owner=c.outsider)
    Resource.objects.load_addons((apps.get_app_config("knowledge"),), tiers=[Resource.Tier.INSTALL])
    with system_context(reason="tests.parties_roles.vault"):
        vault = Vault.objects.for_resource(Vault.PEOPLE)
        folder = RecordBinding.objects.party_folder(c.unlinked, vault=vault, title="Grace Unlinked (folder)")
        note = Page.objects.create_in(vault, parent=folder, title="Grace Unlinked: notes", body="Likes jazz.")
    return SimpleNamespace(
        **vars(c), holders=holders, editor=editor, nobody=nobody, group_manager=group_manager, vault=vault,
        folder=folder, note=note, book=book, circle=circle, membership=membership, task=task,
    )


def _actors(w: SimpleNamespace) -> dict[str, Any]:
    return {
        "nobody": w.nobody,
        "reader": w.holders["contact_reader"],
        "manager": w.holders["contact_manager"],
        "admin": w.holders["contact_admin"],
        "group manager": w.group_manager,
        "owner": w.owner,
        "editor": w.editor,
        "dir reader": w.reader,
        "platform": w.admin,
    }


def _rows(w: SimpleNamespace) -> dict[str, Any]:
    return {
        "person": w.person, "party": w.person_party, "organization": w.organization, "handle": w.own_handle,
        "address": w.address, "relationship": w.edge, "folder": w.book, "circle": w.circle,
        "circle member": w.membership, "vault": w.vault, "vault page": w.note, "task": w.task,
        "directory": w.directory,
    }


# Who holds each action on each row, across every kind of actor: the roles, a member of a group the manager
# role is granted to, the person's owner, a per-record editor of the person, a directory's contact reader and a
# platform admin. Every actor not listed is denied.
EVERY = ("reader", "manager", "admin", "group manager", "owner", "editor", "dir reader", "platform")
ROLE_HOLDERS = ("reader", "manager", "admin", "group manager")
WRITERS = ("manager", "admin", "group manager", "owner", "platform")
ADMINS = ("admin", "owner", "platform")
MATRIX: dict[str, dict[str, tuple[str, ...]]] = {
    "person": {
        "read": EVERY,
        "read_private": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "read__notes": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "read__birthday": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "write": (*WRITERS, "editor"),
        # Merging away retires to the trash: through the roles, the contact admin's (pending ruling).
        "merge_away": (*ADMINS, "editor"),
        "delete": ADMINS,
        # Adoption runs through the roles' own members, never a platform admin's reach of every role.
        "adopt": ("manager", "admin", "group manager", "owner", "editor"),
    },
    "party": {
        "read": EVERY,
        "read_private": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "read__notes": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "write": (*WRITERS, "editor"),
        "merge_away": (*ADMINS, "editor"),
        # Granting a per-record read outlives the granter's role: through the roles, the contact admin's.
        "share": (*ADMINS, "editor"),
        "delete": ADMINS,
        "transfer": ADMINS,
    },
    "organization": {
        "read": (*ROLE_HOLDERS, "owner", "dir reader", "platform"),
        "read_private": (*ROLE_HOLDERS, "owner", "platform"),
        "read__notes": (*ROLE_HOLDERS, "owner", "platform"),
        "write": WRITERS,
        "merge_away": ADMINS,
        "delete": ADMINS,
    },
    "handle": {"read": (*ROLE_HOLDERS, "owner", "platform"), "write": WRITERS, "share": ADMINS, "delete": ADMINS},
    "address": {"read": (*ROLE_HOLDERS, "owner", "dir reader", "platform"), "write": WRITERS, "delete": ADMINS},
    "relationship": {"read": (*ROLE_HOLDERS, "owner", "dir reader", "platform"), "write": WRITERS, "delete": ADMINS},
    "folder": {"read": (*ROLE_HOLDERS, "owner", "platform"), "write": WRITERS, "delete": ADMINS, "transfer": ADMINS},
    "circle": {"read": (*ROLE_HOLDERS, "owner", "platform"), "write": WRITERS, "delete": ADMINS, "transfer": ADMINS},
    "circle member": {"read": (*ROLE_HOLDERS, "owner", "platform"), "write": WRITERS, "delete": ADMINS},
    "vault": {
        "read": (*ROLE_HOLDERS, "platform"),
        "write": ("manager", "admin", "group manager", "platform"),
        "delete": ("admin", "platform"),
        "share": ("platform",),
        "transfer": ("platform",),
    },
    # A person's note in the people vault, written by the system: the vault lends it.
    "vault page": {
        "read": (*ROLE_HOLDERS, "platform"),
        "write": ("manager", "admin", "group manager", "platform"),
        "delete": ("admin", "platform"),
    },
    # A record task about the party (its own owner is someone else): the party lends its per-record holders what
    # they hold on it, and the roles read it but write none (pending ruling, parties/party#role_write_as_subject).
    "task": {
        "read": (*ROLE_HOLDERS, "owner", "editor", "platform"),
        "write": ("owner", "editor", "platform"),
        "share": ("owner", "editor", "platform"),
        "delete": ("owner", "editor", "platform"),
        "transfer": ("owner", "editor", "platform"),
    },
    # A contact admin reads every directory and shares who reads its contacts; changing and removing one stay with
    # its integration's writers and platform admins, creating the row with platform admins.
    "directory": {
        "read": ("admin", "owner", "platform"),
        "share": ("admin", "owner", "platform"),
        "write": ("owner", "platform"),
        "delete": ("owner", "platform"),
        "create": ("platform",),
    },
}


def test_the_decision_table_per_actor(world: SimpleNamespace) -> None:
    """The whole decision table: each listed actor allowed, every other actor denied."""

    actors, rows = _actors(world), _rows(world)
    for name, actions in MATRIX.items():
        for action, allowed in actions.items():
            holders = sorted(label for label, actor in actors.items() if _allowed(rows[name], actor, action))
            assert holders == sorted(allowed), (name, action, holders)


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


def test_a_manager_keeps_people_separate_and_confirms_and_a_contact_admin_merges(world: SimpleNamespace) -> None:
    """The review verbs ride the party's write; a merge trashes the party merged away, so the roles leave it to the
    contact admin (pending ruling, parties/party#role_merge_away)."""

    w = world
    reader, manager, admin = (w.holders[role] for role in ROLES)
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
    with actor_context(manager), pytest.raises(PermissionDenied):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])
    with system_context(reason="tests.parties_roles.merge_refused"):
        assert Party._base_manager.get(pk=merged.pk).merged_into_id is None
    with actor_context(admin):
        Party.objects.get(pk=survivor.pk).merge(records=[merged.public_id])
    with system_context(reason="tests.parties_roles.merge_readback"):
        retired = Party._base_manager.get(pk=merged.pk)
        assert retired.merged_into_id == survivor.pk and retired.is_trashed


def test_a_partyless_handle_is_out_of_every_roles_reach(world: SimpleNamespace) -> None:
    """A mailbox's sender or a calendar's attendee, no party's yet: the roles reach a handle through its party."""

    w = world
    with system_context(reason="tests.parties_roles.partyless"):
        handle = Handle.objects.create(platform="email", value="sender@example.com", created_by=w.outsider)
    assert handle.party_id is None
    for role, actor in w.holders.items():
        for action in ("read", "write", "delete"):
            assert not _allowed(handle, actor, action), (role, action)
        assert not Handle.objects.with_actor(actor).filter(party__isnull=True).exists(), role
    assert _allowed(handle, w.outsider)


def test_a_contact_admin_grants_and_revokes_a_directory_reader_and_a_manager_cannot(world: SimpleNamespace) -> None:
    """The directory's share, not its write: a contact admin manages who reads its contacts; a manager does not."""

    w = world
    admin, manager = w.holders["contact_admin"], w.holders["contact_manager"]
    grantee = get_user_model().objects.create_user(username="role-directory-grantee")
    with actor_context(admin):
        Directory.objects.with_actor(admin).get(pk=w.directory.pk).grant_record_access("contact_reader", grantee)
    assert _allowed(w.person, grantee) and not _allowed(w.person, grantee, "read_private")
    with actor_context(admin):
        Directory.objects.with_actor(admin).get(pk=w.directory.pk).revoke_record_access("contact_reader", grantee)
    assert not _allowed(w.person, grantee)
    with actor_context(manager), pytest.raises(PermissionDenied):
        Directory.objects.sudo(reason="tests.parties_roles.load").get(pk=w.directory.pk).with_actor(
            manager
        ).grant_record_access("contact_reader", grantee)


def test_a_merge_is_refused_to_whoever_does_not_write_both_parties(world: SimpleNamespace) -> None:
    w = world
    with system_context(reason="tests.parties_roles.merge_refusals"):
        survivor = Person.objects.create(display_name="Ada Example", created_by=w.owner)
        merged = Person.objects.create(display_name="Ada E.", created_by=w.owner)
    for actor in (w.holders["contact_reader"], w.reader, w.nobody):
        with actor_context(actor), pytest.raises((PermissionDenied, Person.DoesNotExist, Party.DoesNotExist)):
            Party.objects.with_actor(actor).get(pk=survivor.pk).merge(records=[merged.public_id])
    with system_context(reason="tests.parties_roles.merge_refusals.readback"):
        assert Party._base_manager.get(pk=merged.pk).merged_into_id is None


def test_a_contact_admin_grants_a_per_record_reader_and_a_manager_cannot(world: SimpleNamespace) -> None:
    """A per-record grant outlives its granter's role, so the roles lend sharing to the contact admin alone
    (pending ruling, parties/party#role_share)."""

    w = world
    admin, manager = w.holders["contact_admin"], w.holders["contact_manager"]
    grantee = get_user_model().objects.create_user(username="role-party-grantee")
    for model, pk in ((Party, w.unlinked.pk), (Handle, w.own_handle.pk)):
        with system_context(reason="tests.parties_roles.share_target"):
            row = model._base_manager.get(pk=pk)
        with actor_context(manager), pytest.raises(PermissionDenied):
            model.objects.with_actor(manager).get(pk=pk).grant_record_access("reader", grantee)
        assert not _allowed(row, grantee), model.__name__
        with actor_context(admin):
            model.objects.with_actor(admin).get(pk=pk).grant_record_access("reader", grantee)
        assert _allowed(row, grantee), model.__name__
        with actor_context(admin):
            model.objects.with_actor(admin).get(pk=pk).revoke_record_access("reader", grantee)
        assert not _allowed(row, grantee), model.__name__
