"""Spaces addon models, roster-derived access, and messaging extension tests."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import override_settings
from rebac import PermissionDenied, actor_context, system_context, to_object_ref, to_subject_ref
from rebac.backends import backend
from rebac.models import SchemaRelation, active_relationship_model

from angee.compose.permissions import (
    apply_schema_paths,
    extension_source_map,
    merged_schema_relpath,
    merged_schemas,
    render_zed,
)
from angee.fs import write_atomic
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from tests import test_messaging_graphql
from tests.conftest import (
    Page,
    SchemaAddon,
    Vault,
    assert_private_hasura_insert_access,
    create_user,
    execute_schema,
    installed_field_owners,
    result_data,
    vault_for,
)
from tests.messaging_models import Party, Person, Thread
from tests.projects_models import Queue
from tests.spaces_models import Group, Membership

# These concrete test models register after Django's app population. The lazy
# string relation resolves when ``Party`` registers, but Django may already have
# cached ``related_model`` while the source models were inspected during setup.
Membership._meta.get_field("party").__dict__.pop("related_model", None)
spaces_schema = importlib.import_module("angee.spaces.schema")


@pytest.fixture()
def spaces_tables(transactional_db: Any, tmp_path: Path) -> Iterator[None]:
    """Load the composed spaces/messaging REBAC schema for native test tables."""

    del transactional_db
    app_configs = list(apps.get_app_configs())
    runtime_dir = tmp_path / "runtime"
    source_map = extension_source_map(app_configs, field_owners=installed_field_owners(app_configs))
    for relpath, text in source_map.items():
        write_atomic(runtime_dir / relpath, text)

    originals = {config: getattr(config, "rebac_schema", None) for config in app_configs}
    apply_schema_paths(app_configs, runtime_dir, sources=source_map)

    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        for config, original in originals.items():
            if original is None:
                if hasattr(config, "rebac_schema"):
                    delattr(config, "rebac_schema")
            else:
                config.rebac_schema = original


def _role_relations(group: Group, user: Any) -> set[str]:
    """Return roster roles resolved live for ``user`` on ``group``."""

    # The fixture may still be inside system_context; dispatching an explicit
    # subject to the backend evaluates policy without an ambient sudo bypass.
    return {
        role
        for role in ("owner", "moderator", "member", "viewer")
        if backend().check_access(
            subject=to_subject_ref(user), action=f"roster_{role}", resource=to_object_ref(group),
        ).allowed
    }


def _group_relationship_count(group: Group) -> int:
    """Return every relationship whose resource is ``group``."""

    return active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=str(group.pk),
    ).count()


def _person_for(username: str) -> tuple[Any, Person]:
    """Create one platform user and its canonical parties Person."""

    user = create_user(username)
    with system_context(reason="spaces test identity"):
        person = Person.objects.for_user(user)
    return user, person


def _schema() -> Any:
    """Build the composed console schema used by spaces."""

    modules = (
        test_messaging_graphql.parties_schema,
        test_messaging_graphql.messaging_schema,
        spaces_schema,
    )
    addons = [
        SchemaAddon(
            {
                "console": {
                    key: tuple(module.schemas["console"].get(key, ()))
                    for key in SCHEMA_PART_KEYS
                }
            }
        )
        for module in modules
    ]
    return GraphQLSchemas(addons).build("console")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("rebac_storage", ("denormalized", "registry"))
def test_group_create_ignores_roster_backings_unused_by_create(spaces_tables: None, rebac_storage: str) -> None:
    """The authenticated create arm needs none of the filtered reverse roster paths."""

    del spaces_tables
    actor = create_user("spaces-unused-roster")
    with override_settings(REBAC_LOCAL_BACKEND_STORAGE=rebac_storage):
        call_command("rebac", "sync", verbosity=0)
        result = execute_schema(
            _schema(),
            'mutation { insert_space_groups_one(object: {name: "No roster"}) { id } }',
            user=actor,
        )
        public_id = result_data(result)["insert_space_groups_one"]["id"]
    group = Group._base_manager.get(sqid=public_id)
    assert group.created_by_id == actor.pk
    assert not Membership._base_manager.filter(group=group).exists()


@pytest.mark.django_db(transaction=True)
def test_group_console_insert_establishes_private_owner_access(
    spaces_tables: None,
) -> None:
    """The new group's owner can read and write it; an outsider cannot read it."""

    del spaces_tables
    creator = create_user("spaces-group-creator")
    outsider = create_user("spaces-group-outsider")
    schema = _schema()

    created, readable, updated = assert_private_hasura_insert_access(
        schema,
        creator=creator,
        outsider=outsider,
        create_mutation="""
            mutation CreateGroup {
              insert_space_groups_one(object: {name: "Private"}) {
                id
                name
                slug
              }
            }
            """,
        create_root="insert_space_groups_one",
        detail_query="""
            query Group($id: String!) {
              space_groups_by_pk(id: $id) { id name description }
            }
            """,
        detail_root="space_groups_by_pk",
        update_mutation="""
            mutation UpdateGroup($id: String!) {
              update_space_groups_by_pk(
                pk_columns: {id: $id}
                _set: {description: "Creator write"}
              ) { id description }
            }
            """,
        update_root="update_space_groups_by_pk",
    )
    assert created["name"] == "Private"
    assert created["slug"] == "private"
    assert readable == {"id": created["id"], "name": "Private", "description": ""}
    assert updated == {"id": created["id"], "description": "Creator write"}


@pytest.mark.django_db(transaction=True)
def test_membership_console_insert_inherits_group_owner_access(
    spaces_tables: None,
) -> None:
    """A group's owner can create, read and write roster rows; an outsider cannot."""

    del spaces_tables
    creator = create_user("spaces-membership-creator")
    outsider = create_user("spaces-membership-outsider")
    with system_context(reason="spaces membership GraphQL seed"):
        group = Group._base_manager.create(
            name="Private",
            slug="private-membership",
            created_by=creator,
        )
        party = Party._base_manager.create(display_name="Member", created_by=creator)
    schema = _schema()

    created, readable, updated = assert_private_hasura_insert_access(
        schema,
        creator=creator,
        outsider=outsider,
        create_mutation="""
            mutation CreateMembership($group: ID!, $party: ID!) {
              insert_space_memberships_one(
                object: {group: $group, party: $party, role: "member"}
              ) { id role }
            }
            """,
        create_root="insert_space_memberships_one",
        create_variables={"group": group.sqid, "party": party.sqid},
        detail_query="""
            query Membership($id: String!) {
              space_memberships_by_pk(id: $id) { id role }
            }
            """,
        detail_root="space_memberships_by_pk",
        update_mutation="""
            mutation UpdateMembership($id: String!) {
              update_space_memberships_by_pk(
                pk_columns: {id: $id}
                _set: {role: "moderator"}
              ) { id role }
            }
            """,
        update_root="update_space_memberships_by_pk",
    )
    assert created["role"] == "MEMBER"
    assert readable == {"id": created["id"], "role": "MEMBER"}
    assert updated == {"id": created["id"], "role": "MODERATOR"}


def test_group_crud_slug_uniqueness_and_unscoped_hierarchy(spaces_tables: None) -> None:
    """Groups support hierarchy and share ownership and audience with concrete children."""

    del spaces_tables
    owner, person = _person_for("spaces-child-owner")
    with system_context(reason="spaces group crud"):
        queue = Queue.objects.create(name="Dispatch", key="DSP", created_by=owner)
        Membership.objects.create(group=queue, party=person, is_confirmed=True)
        assert Queue._meta.get_field("owner").model is Group
        assert queue.owner_id == owner.pk
        assert [entry.party_id for entry in queue.thread_audience()] == [person.pk]
        queue.delete()

        root = Group.objects.create(name="Community")
        sibling = Group.objects.create(name="Community")
        child = Group.objects.create(name="Moderators", parent=root)

        assert root.slug == "community"
        assert sibling.slug == "community-2"
        assert child.slug == "moderators"
        assert child.parent == root
        assert child.path.startswith(root.path)

        root.description = "Shared customer community"
        root.save(update_fields=["description", "updated_at"])
        root.refresh_from_db()
        assert root.description == "Shared customer community"

        with pytest.raises(IntegrityError), transaction.atomic():
            Group.objects.create(name="Duplicate", slug="community")

        child.delete()
        sibling.delete()
        root.delete()
        assert Group.objects.count() == 0


def test_membership_crud_and_pair_uniqueness(spaces_tables: None) -> None:
    """A roster has one mutable role-bearing row per group/party pair."""

    del spaces_tables
    with system_context(reason="spaces membership crud"):
        group = Group.objects.create(name="Community", slug="community")
        party = Party.objects.create(display_name="Guest")
        membership = Membership.objects.create(group=group, party=party)

        assert membership.role == Membership.MembershipRole.MEMBER
        membership.role = Membership.MembershipRole.MODERATOR
        membership.save(update_fields=["role", "updated_at"])
        membership.refresh_from_db()
        assert membership.role == Membership.MembershipRole.MODERATOR

        with pytest.raises(IntegrityError), transaction.atomic():
            Membership.objects.create(group=group, party=party)

        membership.delete()
        assert Membership.objects.count() == 0


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
def test_membership_lifecycle_filters_live_roles(
    spaces_tables: None, settings: Any, storage: str,
) -> None:
    """Pending, dismissed and unrelated roles deny in direct and queryset checks."""

    del spaces_tables
    settings.REBAC_LOCAL_BACKEND_STORAGE = storage
    user, person = _person_for("spaces-member")
    other_user, other_person = _person_for("spaces-other-member")

    for role in ("owner", "moderator", "member", "viewer"):
        persisted = SchemaRelation.objects.get(definition__resource_type="spaces/group", name=f"roster_{role}")
        assert persisted.backing == {
            "kind": "fk",
            "path": "memberships__party__person__user",
            "filters": {
                "memberships__is_confirmed": True,
                "memberships__is_dismissed": False,
                "memberships__role": role,
            },
        }

    def assert_roles(group: Group, expected: set[str]) -> None:
        assert _role_relations(group, user) == expected
        assert backend().check_access(
            subject=to_subject_ref(user), action="set_notifications", resource=to_object_ref(membership),
        ).allowed == bool(expected)
        for role in ("owner", "moderator", "member", "viewer"):
            scoped = Group.objects.with_actor(user).with_action(f"roster_{role}").filter(pk=group.pk)
            assert scoped.exists() == (role in expected), str(scoped.query)

    with system_context(reason="spaces membership lifecycle"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(
            group=group,
            party=person,
            role=Membership.MembershipRole.OWNER,
        )
        other_membership = Membership.objects.create(
            group=group, party=other_person, role=Membership.MembershipRole.VIEWER,
        )
        other_membership.confirm()
        assert_roles(group, set())
        assert _role_relations(group, other_user) == {"viewer"}

        membership.confirm()
        assert_roles(group, {"owner"})

        membership.dismiss()
        assert_roles(group, set())

        membership.confirm()
        membership.role = Membership.MembershipRole.MODERATOR
        membership.save(update_fields=["role", "updated_at"])
        assert_roles(group, {"moderator"})

        Membership.objects.filter(pk=membership.pk).delete()
        assert_roles(group, set())


def test_membership_repoint_changes_the_live_holder(spaces_tables: None) -> None:
    """Moving a confirmed roster row makes its new user's live role replace the old one."""

    del spaces_tables
    old_user, old_person = _person_for("spaces-old-member")
    new_user, new_person = _person_for("spaces-new-member")
    with system_context(reason="spaces membership repoint"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(group=group, party=old_person)
        membership.confirm()
        membership.party = new_person
        membership.save(update_fields=["party", "updated_at"])

    assert _role_relations(group, old_user) == set()
    assert _role_relations(group, new_user) == {"member"}


def test_confidence_only_save_leaves_pending_membership_without_roles(spaces_tables: None) -> None:
    """Changing confidence does not grant a pending member any roster role."""

    del spaces_tables
    _user, person = _person_for("spaces-unchanged-member")
    with system_context(reason="spaces unrelated membership save"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(group=group, party=person)

        membership.confidence = 0.5
        membership.save(update_fields=["confidence", "updated_at"])
        assert _role_relations(group, _user) == set()


def test_person_user_change_changes_the_live_roster_holder(spaces_tables: None) -> None:
    """Changing Person.user immediately changes who holds each confirmed roster role."""

    del spaces_tables
    old_user, person = _person_for("spaces-person-old-user")
    new_user = create_user("spaces-person-new-user")
    with system_context(reason="spaces person user change"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(
            group=group,
            party=person,
            role=Membership.MembershipRole.MODERATOR,
        )
        membership.confirm()
        person.user = new_user
        person.save(update_fields=["user", "updated_at"])

    assert _role_relations(group, old_user) == set()
    assert _role_relations(group, new_user) == {"moderator"}


def test_moderator_can_confirm_membership_but_outsider_cannot(spaces_tables: None) -> None:
    """Membership decisions are actor-gated by the containing group's write policy."""

    del spaces_tables
    moderator, moderator_person = _person_for("spaces-confirm-moderator")
    outsider = create_user("spaces-confirm-outsider")
    _accepted_user, accepted_person = _person_for("spaces-confirm-accepted")
    _denied_user, denied_person = _person_for("spaces-confirm-denied")
    with system_context(reason="spaces confirm authorization setup"):
        group = Group.objects.create(name="Community", slug="community")
        moderator_membership = Membership.objects.create(
            group=group,
            party=moderator_person,
            role=Membership.MembershipRole.MODERATOR,
        )
        moderator_membership.confirm()
        accepted = Membership.objects.create(group=group, party=accepted_person)
        denied = Membership.objects.create(group=group, party=denied_person)

    assert not moderator.is_superuser
    with actor_context(moderator):
        accepted.confirm()
    with actor_context(outsider), pytest.raises(PermissionDenied):
        denied.confirm()

    accepted.refresh_from_db()
    denied.refresh_from_db()
    assert accepted.is_confirmed
    assert not denied.is_confirmed


def test_membership_without_a_platform_user_grants_nothing(spaces_tables: None) -> None:
    """A party without Person.user supplies no holder to the real roster relations."""

    del spaces_tables
    user = create_user("spaces-unlinked-party-observer")
    with system_context(reason="spaces membership without user"):
        group = Group.objects.create(name="Community", slug="community")
        party = Party.objects.create(display_name="External contact")
        membership = Membership.objects.create(group=group, party=party)
        membership.confirm()

    for role in ("owner", "moderator", "member", "viewer"):
        assert SchemaRelation.objects.filter(
            definition__resource_type="spaces/group", name=f"roster_{role}",
        ).exists()
        assert not Group.objects.with_actor(user).with_action(f"roster_{role}").filter(pk=group.pk).exists()
    assert _role_relations(group, user) == set()


def test_public_visibility_follows_the_group_column(spaces_tables: None) -> None:
    """Public/private flips change read access without relationship writes."""

    del spaces_tables
    reader = to_subject_ref(create_user("spaces-public-reader"))
    with system_context(reason="spaces visibility"):
        group = Group.objects.create(name="Community", slug="community")
        for visibility, allowed in (
            (Group.GroupVisibility.PRIVATE, False),
            (Group.GroupVisibility.PUBLIC, True),
            (Group.GroupVisibility.PRIVATE, False),
        ):
            group.visibility = visibility
            group.save(update_fields=["visibility", "updated_at"])
            assert backend().check_access(
                subject=reader, action="read", resource=to_object_ref(group),
            ).allowed == allowed
            assert _group_relationship_count(group) == 0


def test_visibility_reads_persisted_facts_including_bulk_writes(spaces_tables: None) -> None:
    """Dirty and deferred values do not replace the persisted visibility column."""

    reader = to_subject_ref(create_user("spaces-persisted-reader"))
    with system_context(reason="spaces visibility persisted policy"):
        group = Group.objects.create(name="Community", visibility=Group.GroupVisibility.PUBLIC)
        group.visibility = Group.GroupVisibility.PRIVATE
        group.description = "Only content changed"
        group.save(update_fields=["description"])
        assert backend().check_access(subject=reader, action="read", resource=to_object_ref(group)).allowed

        group = Group.objects.defer("visibility").get(pk=group.pk)
        group.description = "Deferred policy"
        group.save(update_fields=["description"])
        assert backend().check_access(subject=reader, action="read", resource=to_object_ref(group)).allowed
        group.visibility = Group.GroupVisibility.PRIVATE
        group.save(update_fields=["visibility"])
        assert not backend().check_access(subject=reader, action="read", resource=to_object_ref(group)).allowed

        Group.objects.filter(pk=group.pk).update(visibility=Group.GroupVisibility.PUBLIC)
        assert backend().check_access(subject=reader, action="read", resource=to_object_ref(group)).allowed
        for path in ("", group.path):
            with pytest.raises(ValidationError, match="saved-row owner"):
                Group.objects.bulk_create([
                    Group(name="Public", slug="public", visibility=Group.GroupVisibility.PUBLIC, path=path),
                ])
            assert not Group.objects.filter(slug="public").exists()
        inserted = Group.objects.create(name="Public", slug="public", visibility=Group.GroupVisibility.PUBLIC)
        assert inserted.path == f"/{inserted.pk:0{Group.path_segment_width}d}/"
        assert backend().check_access(subject=reader, action="read", resource=to_object_ref(inserted)).allowed
        assert _group_relationship_count(group) == _group_relationship_count(inserted) == 0


def test_visibility_double_flip_is_idempotent(spaces_tables: None) -> None:
    """Repeated public/private saves change reads without storing tuples."""

    del spaces_tables
    reader = to_subject_ref(create_user("spaces-repeat-reader"))
    with system_context(reason="spaces visibility idempotence"):
        group = Group.objects.create(name="Community", slug="community")
        for visibility, allowed in (
            (Group.GroupVisibility.PUBLIC, True),
            (Group.GroupVisibility.PRIVATE, False),
        ):
            group.visibility = visibility
            for _ in range(2):
                group.save(update_fields=["visibility", "updated_at"])
                assert backend().check_access(
                    subject=reader, action="read", resource=to_object_ref(group),
                ).allowed == allowed
                assert _group_relationship_count(group) == 0


def test_group_delete_removes_roster_rows(spaces_tables: None) -> None:
    """Deleting a public group removes its canonical roster rows."""

    del spaces_tables
    user, person = _person_for("spaces-delete-member")
    with system_context(reason="spaces group delete"):
        group = Group.objects.create(
            name="Community",
            slug="community",
            visibility=Group.GroupVisibility.PUBLIC,
        )
        membership = Membership.objects.create(
            group=group,
            party=person,
            role=Membership.MembershipRole.OWNER,
        )
        membership.confirm()
        assert _role_relations(group, user) == {"owner"}
        assert _group_relationship_count(group) == 0

        resource_id = str(group.pk)
        membership_id = membership.pk
        group.delete()
        assert not Membership.objects.filter(pk=membership_id).exists()

    assert not active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=resource_id,
    ).exists()


def test_group_member_and_viewer_read_bound_group_thread_but_outsider_cannot(
    spaces_tables: None,
) -> None:
    """The messaging fragment grants member/viewer thread read, not unrelated access."""

    del spaces_tables
    member, person = _person_for("spaces-thread-member")
    viewer, viewer_person = _person_for("spaces-thread-viewer")
    outsider = create_user("spaces-thread-outsider")
    with system_context(reason="spaces group thread"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(group=group, party=person)
        membership.confirm()
        viewer_membership = Membership.objects.create(
            group=group,
            party=viewer_person,
            role=Membership.MembershipRole.VIEWER,
        )
        viewer_membership.confirm()
        thread = Thread.objects.create(
            modality=Thread.Modality.GROUP,
        )
        thread.groups.add(group)

    with actor_context(member):
        assert Thread.objects.filter(pk=thread.pk).exists()
    with actor_context(viewer):
        assert Thread.objects.filter(pk=thread.pk).exists()
    with actor_context(outsider):
        assert not Thread.objects.filter(pk=thread.pk).exists()


def test_group_owner_and_moderator_write_bound_thread_but_outsider_cannot(
    spaces_tables: None,
) -> None:
    """The group post capability grants thread writes to owner/moderator, never viewers."""

    del spaces_tables
    owner, owner_person = _person_for("spaces-thread-owner")
    moderator, moderator_person = _person_for("spaces-thread-moderator")
    viewer, viewer_person = _person_for("spaces-thread-write-viewer")
    outsider = create_user("spaces-thread-write-outsider")
    with system_context(reason="spaces group thread write setup"):
        group = Group.objects.create(name="Community", slug="community")
        for person, role in (
            (owner_person, Membership.MembershipRole.OWNER),
            (moderator_person, Membership.MembershipRole.MODERATOR),
            (viewer_person, Membership.MembershipRole.VIEWER),
        ):
            membership = Membership.objects.create(group=group, party=person, role=role)
            membership.confirm()
        thread = Thread.objects.create(modality=Thread.Modality.GROUP)
        thread.groups.add(group)

    for actor, visibility in (
        (owner, Thread.Visibility.PUBLIC),
        (moderator, Thread.Visibility.PRIVATE),
    ):
        with actor_context(actor):
            writable = Thread.objects.get(pk=thread.pk)
            writable.visibility = visibility
            writable.save(update_fields=["visibility", "updated_at"])

    for denied_actor in (viewer, outsider):
        denied = Thread._base_manager.get(pk=thread.pk).with_actor(denied_actor)
        denied.visibility = Thread.Visibility.RESTRICTED
        with pytest.raises(PermissionDenied):
            denied.save(update_fields=["visibility", "updated_at"])


def _vault_scope_pks(actor: Any, action: str) -> tuple[set[Any], str]:
    """Compile the actor's vault permission to one SQL predicate and return its rows."""

    with patch.object(backend(), "accessible", side_effect=AssertionError("enumerated resource IDs")):
        scoped = Vault.objects.with_actor(actor).with_action(action).scoped()
        sql, _params = scoped.order_by().query.sql_with_params()
        return set(scoped.values_list("pk", flat=True)), sql


@pytest.mark.parametrize("storage", ["denormalized", "registry"])
def test_team_vault_follows_the_roster_and_compiles_to_sql(spaces_tables: None, settings: Any, storage: str) -> None:
    """A team's vault: the roster reads, moderators and owners write, viewers and outsiders see nothing."""

    del spaces_tables
    settings.REBAC_LOCAL_BACKEND_STORAGE = storage
    owner, owner_person = _person_for("spaces-vault-owner")
    moderator, moderator_person = _person_for("spaces-vault-moderator")
    member, member_person = _person_for("spaces-vault-member")
    viewer, viewer_person = _person_for("spaces-vault-viewer")
    outsider = create_user("spaces-vault-outsider")
    alice = create_user("spaces-vault-alice")
    private = vault_for(alice, name="Private")
    with system_context(reason="spaces team vault"):
        group = Group.objects.create(name="Managers", slug="managers")
        for person, role in (
            (owner_person, Membership.MembershipRole.OWNER),
            (moderator_person, Membership.MembershipRole.MODERATOR),
            (member_person, Membership.MembershipRole.MEMBER),
            (viewer_person, Membership.MembershipRole.VIEWER),
        ):
            Membership.objects.create(group=group, party=person, role=role, is_confirmed=True)
        vault = Vault.objects.create(name="Handbook", team=group)
        page = Page.objects.create(vault=vault, title="Onboarding")
    assert vault.owner_id is None
    assert _group_relationship_count(group) == 0

    for actor, readable in ((owner, True), (moderator, True), (member, True), (viewer, False), (outsider, False)):
        assert Vault.objects.as_user(actor).filter(pk=vault.pk).exists() is readable
        assert Page.objects.as_user(actor).filter(pk=page.pk).exists() is readable
        assert not Vault.objects.as_user(actor).filter(pk=private.pk).exists()
    assert not Vault.objects.as_user(alice).filter(pk=vault.pk).exists()

    for actor in (owner, moderator):
        with actor_context(actor):
            writable = Vault.objects.as_user(actor).get(pk=vault.pk)
            writable.description = f"edited by {actor.username}"
            writable.save(update_fields=("description",))
    for actor in (member, viewer, outsider):
        denied = Vault._base_manager.get(pk=vault.pk).with_actor(actor)
        denied.description = "vandalised"
        with pytest.raises(PermissionDenied):
            denied.save(update_fields=("description",))
    # Rebinding the team hands the vault to another roster: it follows share, not write.
    rebinding = Vault._base_manager.get(pk=vault.pk).with_actor(owner)
    rebinding.team = None
    with pytest.raises(PermissionDenied):
        rebinding.save(update_fields=("team",))

    for actor, action, expected in (
        (member, "read", {vault.pk}),
        (member, "write", set()),
        (member, "share", set()),
        (moderator, "write", {vault.pk}),
        (viewer, "read", set()),
        (outsider, "read", set()),
        (alice, "read", {private.pk}),
        (alice, "share", {private.pk}),
    ):
        pks, sql = _vault_scope_pks(actor, action)
        assert pks == expected, (actor.username, action)
        # The roster arrives as a join on the live membership rows, never as tuples.
        assert Membership._meta.db_table in sql

    with system_context(reason="spaces team vault unbinding"):
        Vault._base_manager.filter(pk=vault.pk).update(team=None)
    for actor in (owner, moderator, member):
        assert not Vault.objects.as_user(actor).filter(pk=vault.pk).exists()
        assert not Page.objects.as_user(actor).filter(pk=page.pk).exists()
    assert _group_relationship_count(group) == 0


def test_team_member_clones_an_ownerless_template_vault(spaces_tables: None) -> None:
    """A template vault reachable only through its team is cloned by a member, not by an outsider."""

    del spaces_tables
    member, person = _person_for("spaces-template-member")
    outsider = create_user("spaces-template-outsider")
    with system_context(reason="spaces template vault"):
        group = Group.objects.create(name="Managers", slug="managers")
        Membership.objects.create(group=group, party=person, is_confirmed=True)
        template = Vault.objects.create(name="Intake template", team=group)
        Page.objects.create(vault=template, title="Checklist", kind=Page.Kind.TEMPLATE)
    assert template.owner_id is None

    with actor_context(member):
        clone = Vault.objects.create_from(template, name="Intake 12")
    assert (clone.owner_id, clone.team_id) == (member.pk, None)
    assert [row.title for row in Page.objects.as_user(member).filter(vault=clone)] == ["Checklist"]
    with actor_context(outsider), pytest.raises(Vault.DoesNotExist):
        Vault.objects.create_from(template, name="Taken")


def test_spaces_fragment_binds_the_vault_to_its_team_roster() -> None:
    """The composed vault reads and writes through its team; delete and share stay the owner's."""

    app_configs = list(apps.get_app_configs())
    merged = merged_schemas(app_configs, field_owners=installed_field_owners(app_configs))
    knowledge = merged["angee.knowledge"]
    definition = knowledge.get_definition("knowledge/vault")
    assert definition is not None
    assert "team" in {relation.name for relation in definition.relations}

    rendered = render_zed("angee.knowledge", knowledge)
    assert "relation team: spaces/group // rebac:field=team" in rendered
    block = rendered.split("definition knowledge/vault {", maxsplit=1)[1].split("\n}", maxsplit=1)[0]
    permissions = {
        line.split("=", maxsplit=1)[0].split()[1]: line
        for line in block.splitlines()
        if line.strip().startswith("permission ")
    }
    assert "team->post" in permissions["read"]
    assert "team->write" in permissions["write"]
    assert "share" in permissions["write__team"]
    assert "team" not in permissions["delete"]
    assert "team" not in permissions["share"]


def test_spaces_fragment_merges_only_read_and_write_into_messaging_thread() -> None:
    """The composed thread derives group access only from its selected groups."""

    app_configs = list(apps.get_app_configs())
    field_owners = installed_field_owners(app_configs)
    merged = merged_schemas(app_configs, field_owners=field_owners)
    messaging = merged["angee.messaging"]
    definition = messaging.get_definition("messaging/thread")
    assert definition is not None
    assert {relation.name for relation in definition.relations} >= {"selected_group"}
    assert "group" not in {relation.name for relation in definition.relations}

    rendered = render_zed("angee.messaging", messaging)
    assert "relation group: spaces/group" not in rendered
    assert "relation selected_group: spaces/group // rebac:field=groups" in rendered
    assert "selected_group->read" in rendered
    assert "selected_group->post" in rendered

    thread_block = rendered.split("definition messaging/thread {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    delete_line = next(line for line in thread_block.splitlines() if "permission delete" in line)
    assert "group" not in delete_line
    assert merged_schema_relpath("angee.messaging") in extension_source_map(app_configs, field_owners=field_owners)
