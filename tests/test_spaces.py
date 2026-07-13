"""Spaces addon models, roster-derived access, and messaging extension tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from rebac import PermissionDenied, actor_context, system_context
from rebac.models import active_relationship_model

from angee.compose.permissions import (
    apply_schema_paths,
    extension_source_map,
    merged_schema_relpath,
    merged_schemas,
    render_zed,
)
from angee.fs import write_atomic
from tests.conftest import _clear_model_tables, _create_missing_tables, create_user
from tests.spaces_models import Group, Membership
from tests.test_messaging import Party, Person, Thread

SPACES_TEST_MODELS = (Party, Person, Group, Membership, Thread)


@pytest.fixture()
def spaces_tables(transactional_db: Any, tmp_path: Path) -> Iterator[None]:
    """Create concrete tables and load the composed spaces/messaging REBAC schema."""

    del transactional_db
    app_configs = list(apps.get_app_configs())
    runtime_dir = tmp_path / "runtime"
    for relpath, text in extension_source_map(app_configs).items():
        write_atomic(runtime_dir / relpath, text)

    messaging = apps.get_app_config("messaging")
    sentinel = object()
    original_schema = getattr(messaging, "rebac_schema", sentinel)
    apply_schema_paths(app_configs, runtime_dir)

    created_models = _create_missing_tables(SPACES_TEST_MODELS)
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        _clear_model_tables(SPACES_TEST_MODELS)
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)
        if original_schema is sentinel:
            if hasattr(messaging, "rebac_schema"):
                delattr(messaging, "rebac_schema")
        else:
            messaging.rebac_schema = original_schema


def _role_relations(group: Group, user: Any) -> set[str]:
    """Return the direct roster roles currently granted to ``user`` on ``group``."""

    return set(
        active_relationship_model()
        .objects.filter(
            resource_type="spaces/group",
            resource_id=group.sqid,
            relation__in=("owner", "moderator", "member"),
            subject_type="auth/user",
            subject_id=user.sqid,
        )
        .values_list("relation", flat=True)
    )


def _wildcard_reader_exists(group: Group) -> bool:
    """Return whether ``group`` carries its public-reader wildcard tuple."""

    return active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=group.sqid,
        relation="reader",
        subject_type="auth/user",
        subject_id="*",
    ).exists()


def _group_relationship_count(group: Group) -> int:
    """Return every relationship whose resource is ``group``."""

    return active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=group.sqid,
    ).count()


def _person_for(username: str) -> tuple[Any, Person]:
    """Create one platform user and its canonical parties Person."""

    user = create_user(username)
    with system_context(reason="spaces test identity"):
        person = Person.objects.for_user(user)
    return user, person


def test_group_crud_slug_uniqueness_and_unscoped_hierarchy(spaces_tables: None) -> None:
    """Groups persist, update, delete, reject duplicate slugs, and nest without a scope."""

    del spaces_tables
    with system_context(reason="spaces group crud"):
        root = Group.objects.create(name="Community", slug="community")
        child = Group.objects.create(name="Moderators", slug="moderators", parent=root)

        assert child.parent == root
        assert child.path.startswith(root.path)

        root.description = "Shared customer community"
        root.save(update_fields=["description", "updated_at"])
        root.refresh_from_db()
        assert root.description == "Shared customer community"

        with pytest.raises(IntegrityError), transaction.atomic():
            Group.objects.create(name="Duplicate", slug="community")

        child.delete()
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


def test_membership_lifecycle_reconciles_role_relationships(spaces_tables: None) -> None:
    """Confirm, dismiss, role change, and queryset delete keep one direct role tuple."""

    del spaces_tables
    user, person = _person_for("spaces-member")
    with system_context(reason="spaces membership lifecycle"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(
            group=group,
            party=person,
            role=Membership.MembershipRole.OWNER,
        )
        assert _role_relations(group, user) == set()

        membership.confirm()
        assert _role_relations(group, user) == {"owner"}

        membership.dismiss()
        assert _role_relations(group, user) == set()

        membership.confirm()
        membership.role = Membership.MembershipRole.MODERATOR
        membership.save(update_fields=["role", "updated_at"])
        assert _role_relations(group, user) == {"moderator"}

        Membership.objects.filter(pk=membership.pk).delete()
        assert _role_relations(group, user) == set()


def test_membership_repoint_revokes_the_stored_subject(spaces_tables: None) -> None:
    """Moving a confirmed roster row revokes the old user before granting the new one."""

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


def test_unrelated_membership_save_skips_subject_resolution(
    spaces_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confidence-only save performs no REBAC subject resolution or tuple writes."""

    del spaces_tables
    _user, person = _person_for("spaces-unchanged-member")
    with system_context(reason="spaces unrelated membership save"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(group=group, party=person)

        def unexpected_resolution() -> None:
            raise AssertionError("unrelated save resolved the membership subject")

        monkeypatch.setattr(membership, "_role_subject", unexpected_resolution)
        membership.confidence = 0.5
        membership.save(update_fields=["confidence", "updated_at"])


def test_person_user_change_reconciles_membership_subject(spaces_tables: None) -> None:
    """Changing Person.user migrates each confirmed membership grant to the new user."""

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

    membership.refresh_from_db()
    assert membership.granted_user_id == new_user.pk
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
    """A valid party roster row with no Person.user identity never writes an access tuple."""

    del spaces_tables
    with system_context(reason="spaces membership without user"):
        group = Group.objects.create(name="Community", slug="community")
        party = Party.objects.create(display_name="External contact")
        membership = Membership.objects.create(group=group, party=party)
        membership.confirm()

    assert not active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=group.sqid,
        relation__in=("owner", "moderator", "member"),
    ).exists()


def test_public_visibility_reconciles_the_wildcard_reader(spaces_tables: None) -> None:
    """Public/private flips add and remove ``reader@auth/user:*``."""

    del spaces_tables
    with system_context(reason="spaces visibility"):
        group = Group.objects.create(name="Community", slug="community")
        assert not _wildcard_reader_exists(group)

        group.visibility = Group.GroupVisibility.PUBLIC
        group.save(update_fields=["visibility", "updated_at"])
        assert _wildcard_reader_exists(group)

        group.visibility = Group.GroupVisibility.PRIVATE
        group.save(update_fields=["visibility", "updated_at"])
        assert not _wildcard_reader_exists(group)


def test_visibility_double_flip_is_idempotent(spaces_tables: None) -> None:
    """Repeated public/private reconciliation creates no duplicate or stale tuple."""

    del spaces_tables
    with system_context(reason="spaces visibility idempotence"):
        group = Group.objects.create(name="Community", slug="community")
        group.visibility = Group.GroupVisibility.PUBLIC
        group.save(update_fields=["visibility", "updated_at"])
        group.save(update_fields=["visibility", "updated_at"])
        assert _group_relationship_count(group) == 1

        group.visibility = Group.GroupVisibility.PRIVATE
        group.save(update_fields=["visibility", "updated_at"])
        group.save(update_fields=["visibility", "updated_at"])
        assert _group_relationship_count(group) == 0


def test_group_delete_revokes_membership_and_group_relationships(spaces_tables: None) -> None:
    """Deleting a public group removes its wildcard and every roster role tuple."""

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
        assert _wildcard_reader_exists(group)
        assert _role_relations(group, user) == {"owner"}
        assert _group_relationship_count(group) == 2

        resource_id = group.sqid
        group.delete()

    assert not active_relationship_model().objects.filter(
        resource_type="spaces/group",
        resource_id=resource_id,
    ).exists()


def test_group_member_reads_bound_group_thread_but_outsider_cannot(spaces_tables: None) -> None:
    """The messaging fragment grants a non-admin member, not an unrelated actor, thread read."""

    del spaces_tables
    member, person = _person_for("spaces-thread-member")
    outsider = create_user("spaces-thread-outsider")
    with system_context(reason="spaces group thread"):
        group = Group.objects.create(name="Community", slug="community")
        membership = Membership.objects.create(group=group, party=person)
        membership.confirm()
        thread = Thread.objects.create(
            modality=Thread.Modality.GROUP,
            group=group,
        )

    with actor_context(member):
        assert Thread.objects.filter(pk=thread.pk).exists()
    with actor_context(outsider):
        assert not Thread.objects.filter(pk=thread.pk).exists()


def test_group_owner_and_moderator_write_bound_thread_but_outsider_cannot(
    spaces_tables: None,
) -> None:
    """The group post capability grants thread writes to owner/moderator, never outsiders."""

    del spaces_tables
    owner, owner_person = _person_for("spaces-thread-owner")
    moderator, moderator_person = _person_for("spaces-thread-moderator")
    outsider = create_user("spaces-thread-write-outsider")
    with system_context(reason="spaces group thread write setup"):
        group = Group.objects.create(name="Community", slug="community")
        for person, role in (
            (owner_person, Membership.MembershipRole.OWNER),
            (moderator_person, Membership.MembershipRole.MODERATOR),
        ):
            membership = Membership.objects.create(group=group, party=person, role=role)
            membership.confirm()
        thread = Thread.objects.create(modality=Thread.Modality.GROUP, group=group)

    for actor, visibility in (
        (owner, Thread.Visibility.PUBLIC),
        (moderator, Thread.Visibility.PRIVATE),
    ):
        with actor_context(actor):
            writable = Thread.objects.get(pk=thread.pk)
            writable.visibility = visibility
            writable.save(update_fields=["visibility", "updated_at"])

    denied = Thread._base_manager.get(pk=thread.pk).with_actor(outsider)
    denied.visibility = Thread.Visibility.RESTRICTED
    with pytest.raises(PermissionDenied):
        denied.save(update_fields=["visibility", "updated_at"])


def test_spaces_fragment_merges_only_read_and_write_into_messaging_thread() -> None:
    """The composed messaging definition carries the group relation and only legal arms."""

    merged = merged_schemas(apps.get_app_configs())
    messaging = merged["angee.messaging"]
    definition = messaging.get_definition("messaging/thread")
    assert definition is not None
    assert {relation.name for relation in definition.relations} >= {"group"}

    rendered = render_zed("angee.messaging", messaging)
    assert "relation group: spaces/group // rebac:field=group" in rendered
    assert "group->read" in rendered
    assert "group->post" in rendered

    thread_block = rendered.split("definition messaging/thread {", maxsplit=1)[1].split(
        "\n}", maxsplit=1
    )[0]
    delete_line = next(line for line in thread_block.splitlines() if "permission delete" in line)
    assert "group" not in delete_line
    assert merged_schema_relpath("angee.messaging") in extension_source_map(apps.get_app_configs())
