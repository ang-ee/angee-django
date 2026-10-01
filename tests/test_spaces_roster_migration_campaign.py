"""Populated historical roster conversion in both stores, on native test tables."""

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState
from rebac import system_context

from angee.spaces.runtime_migrations import drop_stored_roster as conversion
from angee.spaces.runtime_migrations import owner_column
from tests.conftest import create_user
from tests.iam_models import Group as IAMGroup
from tests.messaging_models import Person, Thread
from tests.spaces_models import Group, Membership
from tests.test_spaces import _person_for
from tests.test_spaces import spaces_tables as spaces_tables

SUMMARY_KEYS = (
    "people_created", "roster_rows_created", "roster_rows_existing_sufficient", "roster_rows_existing_reduced",
    "thread_links_created", "thread_links_existing", "skipped_subjects", "non_granting_retired_tuples",
    "non_granting_group_member_matches", "retired_tuples_deleted",
)
CONDITIONS = (
    {"expires_at": datetime(2000, 1, 1, tzinfo=UTC)},
    {"expires_at": datetime(2100, 1, 1, tzinfo=UTC)},
    {"caveat_name": "conditional", "caveat_context": {"allowed": True}},
)


@pytest.fixture
def historical_roster(spaces_tables):
    """Django renders the shared composition as migration-state models.

    Retain the real alias-bearing ordering (including sqid) to detect historical
    queries that accidentally ask a runtime manager to prepare those aliases.
    No parallel schema, live manager, or table lifecycle is introduced.
    """
    state = ProjectState.from_apps(apps)
    return state


@pytest.fixture(params=("Relationship", "RelationshipRegistry"))
def legacy_store(request, historical_roster):
    return request.param


def legacy_tuple(state, store, target, role, subject, subject_relation="", **conditions):
    """Insert historical wire facts without current-schema validation."""
    model = state.apps.get_model("rebac", store)
    values = {"relation": role, "optional_subject_relation": subject_relation, **conditions}
    if store == "Relationship":
        values.update(resource_type=target[0], resource_id=str(target[1]),
                      subject_type=subject[0], subject_id=str(subject[1]))
    else:
        resources = state.apps.get_model("rebac", "RebacResource")._base_manager.order_by()
        values["resource_fk_id"] = resources.get_or_create(
            resource_type=target[0], resource_id=str(target[1]),
        )[0].pk
        values["subject_fk_id"] = resources.get_or_create(
            resource_type=subject[0], resource_id=str(subject[1]),
        )[0].pk
    return model._base_manager.create(**values)


def run_conversion(state, caplog, *, warning=False, **counts):
    """Check the exact observable summary, including every zero-valued counter."""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=conversion.__name__), connection.schema_editor() as editor:
        conversion.forwards(state.apps, editor)
    expected = "Stored roster conversion: " + " ".join(f"{key}={counts.get(key, 0)}" for key in SUMMARY_KEYS)
    assert [record for record in caplog.record_tuples if record[0] == conversion.__name__] == [
        (conversion.__name__, logging.WARNING if warning else logging.INFO, expected),
    ]


def snapshot(state):
    """Compare canonical data and both stores without invoking runtime ordering."""
    return {
        label: list(state.apps.get_model(*label)._base_manager.order_by("pk").values())
        for label in (("parties", "party"), ("parties", "person"), ("spaces", "membership"),
                      ("rebac", "relationship"), ("rebac", "relationshipregistry"),
                      ("messaging", "thread_groups"))
    }


def test_each_retired_role_becomes_one_confirmed_roster_row_and_retry_is_noop(
    historical_roster, legacy_store, caplog, capsys,
):
    state = historical_roster
    with system_context(reason="historical roles"):
        group = Group.objects.create(name="Legacy team")
    expected = {}
    for role in ("owner", "moderator", "member", "viewer"):
        user, person = _person_for(f"legacy-{role}")
        expected[person.pk] = role
        legacy_tuple(state, legacy_store, ("spaces/group", group.pk), role, ("auth/user", user.pk))
    run_conversion(state, caplog, roster_rows_created=4, retired_tuples_deleted=4)
    assert set(Membership._base_manager.values_list("party_id", "role", "is_confirmed", "is_dismissed")) == {
        (pk, role, True, False) for pk, role in expected.items()
    }
    before = snapshot(state)
    run_conversion(state, caplog)
    assert snapshot(state) == before
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("names,expected", (
    ({"first_name": " Ada", "last_name": "Lovelace "}, "Ada Lovelace"),
    ({"username": "  fallback-name  "}, "fallback-name"),
    ({"username": " ", "email": " mail@example.com "}, "mail@example.com"),
    ({"username": " ", "email": " "}, None),
))
def test_missing_person_uses_frozen_owner_display_name_defaults_and_attribution(
    historical_roster, legacy_store, caplog, names, expected,
):
    state = historical_roster
    user = create_user("missing-person")
    user_model = state.apps.get_model(settings.AUTH_USER_MODEL)
    user_model._base_manager.filter(pk=user.pk).update(**names)
    assert not Person._base_manager.filter(user_id=user.pk).exists()
    with system_context(reason="legacy missing person"):
        group = Group.objects.create(name="Missing person team")
    legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "member", ("auth/user", user.pk))
    run_conversion(state, caplog, people_created=1, roster_rows_created=1, retired_tuples_deleted=1)
    person = Person._base_manager.get(user_id=user.pk)
    assert person.display_name == (expected if expected is not None else str(user.pk))
    assert person.created_by_id == user.pk
    assert person.given_name == Person._meta.get_field("given_name").get_default()
    assert person.family_name == Person._meta.get_field("family_name").get_default()
    assert Membership._base_manager.get(group=group, party=person).is_confirmed


def test_strongest_role_wins_across_both_stores_and_existing_rows_win_unchanged(historical_roster, caplog):
    state = historical_roster
    with system_context(reason="legacy precedence"):
        group = Group.objects.create(name="Precedence")
    user, person = _person_for("multiple-roles")
    for store, roles in (("Relationship", ("viewer", "moderator")), ("RelationshipRegistry", ("member", "owner"))):
        for role in roles:
            legacy_tuple(state, store, ("spaces/group", group.pk), role, ("auth/user", user.pk))
    run_conversion(state, caplog, roster_rows_created=1, roster_rows_existing_sufficient=3, retired_tuples_deleted=4)
    assert Membership._base_manager.get(party=person).role == "owner"


def test_existing_equal_stronger_weaker_pending_and_dismissed_rows_are_preserved(
    historical_roster, legacy_store, caplog,
):
    state = historical_roster
    with system_context(reason="existing roster rows"):
        group = Group.objects.create(name="Existing roster")
    for seat, role, confirmed, dismissed in (
        ("equal", "moderator", True, False), ("stronger", "owner", True, False),
        ("weaker", "viewer", True, False), ("pending", "owner", False, False),
        ("dismissed", "owner", True, True),
    ):
        user, person = _person_for(f"existing-{seat}")
        with system_context(reason="existing membership"):
            Membership.objects.create(group=group, party=person, role=role, is_confirmed=confirmed,
                                      is_dismissed=dismissed, notification_policy="muted", subtype_keys=["comment"])
        legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "moderator", ("auth/user", user.pk))
    before = list(state.apps.get_model("spaces", "Membership")._base_manager.order_by("pk").values())
    run_conversion(state, caplog, warning=True, roster_rows_existing_sufficient=2,
                   roster_rows_existing_reduced=3, retired_tuples_deleted=5)
    assert list(state.apps.get_model("spaces", "Membership")._base_manager.order_by("pk").values()) == before


def test_unrecognized_existing_role_is_preserved_and_reported_as_reduced(historical_roster, legacy_store, caplog):
    user, person = _person_for("unknown-legacy-role")
    with system_context(reason="unknown historical roster role"):
        group = Group.objects.create(name="Historical role")
        row = Membership.objects.create(group=group, party=person, is_confirmed=True)
    # Legacy database values may outlive their Python choice catalogue.
    table = connection.ops.quote_name(Membership._meta.db_table)
    with connection.cursor() as cursor:
        cursor.execute(f"UPDATE {table} SET role = %s WHERE id = %s", ["retired", row.pk])
    legacy_tuple(historical_roster, legacy_store, ("spaces/group", group.pk), "owner", ("auth/user", user.pk))
    run_conversion(historical_roster, caplog, warning=True, roster_rows_existing_reduced=1, retired_tuples_deleted=1)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT role FROM {table} WHERE id = %s", [row.pk])
        assert cursor.fetchone() == ("retired",)


def test_conditional_roster_thread_and_iam_grants_never_become_permanent_access(
    historical_roster, legacy_store, caplog,
):
    state = historical_roster
    with system_context(reason="conditional legacy grants"):
        group = Group.objects.create(name="Conditional team")
        iam = IAMGroup.objects.create(name="Conditional IAM")
    iam_rows = []
    for index, condition in enumerate(CONDITIONS):
        user = create_user(f"conditional-{index}")
        with system_context(reason="conditional thread"):
            thread = Thread.objects.create()
        legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "owner", ("auth/user", user.pk), **condition)
        legacy_tuple(state, legacy_store, ("messaging/thread", thread.pk), "group",
                     ("spaces/group", group.pk), **condition)
        iam_rows.append(legacy_tuple(state, legacy_store, ("auth/group", iam.pk), "member",
                                     ("auth/user", user.pk), **condition).pk)
    legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "member", ("auth/group", iam.pk), "member")
    run_conversion(state, caplog, warning=True, non_granting_retired_tuples=6,
                   non_granting_group_member_matches=3, retired_tuples_deleted=7)
    assert not Membership._base_manager.exists()
    assert not Person._base_manager.exists()
    assert not thread.groups.through._base_manager.exists()
    assert set(state.apps.get_model("rebac", legacy_store)._base_manager.order_by()
               .values_list("pk", flat=True)) == set(iam_rows)


def test_iam_expansion_snapshots_current_direct_users_in_both_stores(historical_roster, legacy_store, caplog):
    state = historical_roster
    with system_context(reason="legacy IAM roster"):
        group = Group.objects.create(name="Snapshot team")
        iam = IAMGroup.objects.create(name="Snapshot IAM")
    users = [create_user(f"snapshot-{index}") for index in range(2)]
    member_tuples = [legacy_tuple(state, store, ("auth/group", iam.pk), "member", ("auth/user", user.pk))
                     for store, user in zip(("Relationship", "RelationshipRegistry"), users, strict=True)]
    legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "moderator", ("auth/group", iam.pk), "member")
    run_conversion(state, caplog, people_created=2, roster_rows_created=2, retired_tuples_deleted=1)
    assert set(Person._base_manager.values_list("user_id", flat=True)) == {user.pk for user in users}
    before = list(Membership._base_manager.order_by("pk").values())
    for row in member_tuples:
        row.delete()
    later = create_user("snapshot-later")
    legacy_tuple(state, legacy_store, ("auth/group", iam.pk), "member", ("auth/user", later.pk))
    run_conversion(state, caplog)
    assert list(Membership._base_manager.order_by("pk").values()) == before
    assert not Person._base_manager.filter(user_id=later.pk).exists()


def test_invalid_subjects_and_targets_are_counted_deleted_and_do_not_create_people(
    historical_roster, legacy_store, caplog,
):
    state = historical_roster
    user = create_user("valid-unconverted-user")
    with system_context(reason="invalid legacy subjects"):
        group = Group.objects.create(name="Invalid subjects")
        empty = IAMGroup.objects.create(name="Empty IAM")
        nested = IAMGroup.objects.create(name="Nested IAM")
        thread = Thread.objects.create()
    for kind, identifier, relation in (
        ("auth/user", "not-an-id", ""), ("auth/user", 999999, ""), ("auth/user", "*", ""),
        ("auth/user", user.pk, "member"), ("service/principal", "service", ""),
        ("auth/group", empty.pk, "member"), ("auth/group", nested.pk, "member"),
        ("auth/group", 999999, "member"),
    ):
        legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "owner", (kind, identifier), relation)
    legacy_tuple(state, legacy_store, ("auth/group", nested.pk), "member", ("auth/group", empty.pk), "member")
    legacy_tuple(state, legacy_store, ("spaces/group", 999999), "member", ("auth/user", user.pk))
    legacy_tuple(state, legacy_store, ("messaging/thread", 999999), "group", ("spaces/group", group.pk))
    legacy_tuple(state, legacy_store, ("messaging/thread", thread.pk), "group", ("spaces/group", 999999))
    legacy_tuple(state, legacy_store, ("messaging/thread", thread.pk), "group", ("auth/user", user.pk))
    run_conversion(state, caplog, warning=True, skipped_subjects=12, retired_tuples_deleted=12)
    assert not Membership._base_manager.exists()
    assert not Person._base_manager.exists()


def test_thread_links_are_converted_once_and_unrelated_relationships_survive(historical_roster, legacy_store, caplog):
    state = historical_roster
    with system_context(reason="historical thread links"):
        group = Group.objects.create(name="Linked group")
        threads = [Thread.objects.create(), Thread.objects.create()]
        threads[0].groups.add(group)
    for thread in threads:
        legacy_tuple(state, legacy_store, ("messaging/thread", thread.pk), "group", ("spaces/group", group.pk))
    unrelated = [
        legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "reader", ("auth/user", "*")),
        legacy_tuple(state, legacy_store, ("messaging/thread", threads[0].pk), "reader", ("auth/user", "*")),
        legacy_tuple(state, legacy_store, ("other/record", "123"), "owner", ("auth/user", "456")),
    ]
    run_conversion(state, caplog, thread_links_created=1, thread_links_existing=1, retired_tuples_deleted=2)
    assert set(threads[0].groups.through._base_manager.values_list("thread_id", "group_id")) == {
        (thread.pk, group.pk) for thread in threads
    }
    assert set(state.apps.get_model("rebac", legacy_store)._base_manager.order_by().values_list("pk", flat=True)) == {
        row.pk for row in unrelated
    }
    before = snapshot(state)
    run_conversion(state, caplog)
    assert snapshot(state) == before


def test_absent_historical_iam_model_skips_group_subject(historical_roster, legacy_store, caplog):
    state = historical_roster.clone()
    state.remove_model("iam", "group")
    with system_context(reason="legacy without IAM groups"):
        group = Group.objects.create(name="No IAM model")
    legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "member", ("auth/group", "12"), "member")
    run_conversion(state, caplog, warning=True, skipped_subjects=1, retired_tuples_deleted=1)


@pytest.mark.parametrize("denied", ("spaces.membership", "parties.person", "messaging.thread_groups"))
def test_router_refusal_preserves_all_legacy_and_canonical_rows(historical_roster, legacy_store, denied, monkeypatch):
    state = historical_roster
    user = create_user("routed-user")
    with system_context(reason="routed roster"):
        group = Group.objects.create(name="Routed team")
    legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "member", ("auth/user", user.pk))
    calls = []

    def allow(alias, model):
        calls.append((alias, model._meta.label_lower))
        return model._meta.label_lower != denied

    before = snapshot(state)
    with monkeypatch.context() as patcher:
        patcher.setattr(conversion.router, "allow_migrate_model", allow)
        conversion.forwards(state.apps, SimpleNamespace(connection=connection))
    assert snapshot(state) == before
    assert (connection.alias, denied) in calls
    assert all(alias == connection.alias for alias, _ in calls)


def test_store_router_refusal_leaves_that_stores_retired_tuples_untouched(historical_roster, legacy_store, monkeypatch):
    state = historical_roster
    user = create_user("store-routed-user")
    with system_context(reason="store-routed roster"):
        group = Group.objects.create(name="Store-routed team")
    row = legacy_tuple(state, legacy_store, ("spaces/group", group.pk), "member", ("auth/user", user.pk))
    with monkeypatch.context() as patcher:
        patcher.setattr(conversion.router, "allow_migrate_model",
                        lambda alias, model: model._meta.model_name != legacy_store.lower())
        conversion.forwards(state.apps, SimpleNamespace(connection=connection))
    assert type(row)._base_manager.filter(pk=row.pk).exists()
    assert not Membership._base_manager.exists()
    assert not Person._base_manager.exists()


def test_empty_roster_upgrade_applies_and_runs_as_noop(historical_roster, caplog):
    assert conversion.applies(historical_roster)
    assert not conversion.applies(ProjectState())
    before = snapshot(historical_roster)
    run_conversion(historical_roster, caplog)
    assert snapshot(historical_roster) == before


def test_owner_backfill_preserves_attribution_and_refuses_lossy_reversal(historical_roster):
    creator = create_user("legacy-creator")
    other = create_user("legacy-new-owner")
    with system_context(reason="historical ownership"):
        group = Group.objects.create(name="Attributed", created_by=creator)
        ownerless = Group.objects.create(name="Unattributed")
    historical = historical_roster.apps.get_model("spaces", "Group")
    historical._base_manager.filter(pk=group.pk).update(owner_id=None)
    editor = SimpleNamespace(connection=connection)
    owner_column.forwards(historical_roster.apps, editor)
    owner_column.forwards(historical_roster.apps, editor)
    assert historical._base_manager.order_by().get(pk=group.pk).owner_id == creator.pk
    assert historical._base_manager.order_by().get(pk=ownerless.pk).owner_id is None
    owner_column.backwards(historical_roster.apps, editor)
    for new_owner in (other.pk, None):
        historical._base_manager.filter(pk=group.pk).update(owner_id=new_owner)
        with pytest.raises(IrreversibleError, match="Transferred or cleared"):
            owner_column.backwards(historical_roster.apps, editor)
        assert historical._base_manager.order_by().get(pk=group.pk).created_by_id == creator.pk
