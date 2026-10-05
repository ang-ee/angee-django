"""Managed people: account verbs, protected accounts, field gates and the verbs' projection."""

from typing import Any

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.base.mixins import StaleRevisionError
from angee.spaces.testing.models import Group, Membership
from tests.conftest import create_platform_admin, execute_schema, graphql_request, result_data
from tests.iam_campaign import Person
from tests.iam_campaign import iam_admin as iam_admin
from tests.test_iam_graphql import _schema
from tests.test_spaces import spaces_tables as spaces_tables

User = get_user_model()
VERBS = ("set_active", "rename", "reset_password", "issue_password")
UPDATE = """mutation($id: String!, $set: users_set_input!, $revision: Int) {
  update_users_by_pk(pk_columns: {id: $id}, _set: $set, expected_revision: $revision) { id revision }
}"""
SET_ACTIVE = """mutation($id: ID!, $active: Boolean!, $confirmed: Boolean!, $revision: Int!) {
  set_user_active(id: $id, active: $active, confirmed: $confirmed, expected_revision: $revision) {
    ok message code validation_errors
  }
}"""
RENAME = """mutation($id: ID!, $first: String!, $last: String!, $revision: Int!) {
  rename_user(id: $id, first_name: $first, last_name: $last, confirmed: true, expected_revision: $revision) {
    ok message
  }
}"""
RESET = """mutation($id: ID!, $confirmed: Boolean!, $revision: Int!) {
  reset_user_password(id: $id, confirmed: $confirmed, expected_revision: $revision) { username password }
}"""
ISSUE = "mutation($id: ID!) { issue_user_password(id: $id) { username password } }"
LIST = "{ users(limit: 100, order_by: [{username: asc}]) { username account_actions last_login revision } }"


def _manager(username: str) -> Any:
    """Return a person holding the extension example's roster-backed manager role."""

    actor = User.objects.create_user(username, f"{username}@example.com")
    with system_context(reason="test.account.manager"):
        group = Group.objects.create(name=f"Managers {username}", slug=f"managers-{username}")
        membership = Membership.objects.create(group=group, party=Person.objects.for_user(actor), role="owner")
        membership.confirm()
    write_relationships([
        RelationshipTuple(
            resource=ObjectRef("extcontrib/role", "manager"),
            relation="team",
            subject=SubjectRef.of("spaces/group", str(group.pk)),
        ),
    ])
    return actor


def _stored(user: Any) -> Any:
    return User._base_manager.get(pk=user.pk)


def _actions(schema: Any, viewer: Any) -> dict[str, list[str]]:
    rows = result_data(execute_schema(schema, LIST, user=viewer))["users"]
    return {row["username"]: [action.lower() for action in row["account_actions"]] for row in rows}


def test_field_gates_keep_identity_and_authority_columns_with_administrators(spaces_tables):
    admin = create_platform_admin("gate-admin", password=None)
    manager = _manager("gate-manager")
    target = User.objects.create_user("gated", "gated@example.com", "a-password")
    schema = _schema("console")
    target_id = target.public_id

    renamed = execute_schema(schema, UPDATE, {"id": target_id, "set": {"first_name": "Renamed"}}, user=manager)
    assert result_data(renamed)["update_users_by_pk"]["revision"] == _stored(target).account_revision
    assert _stored(target).first_name == "Renamed"
    for change in ({"email": "moved@example.com"}, {"username": "moved"}, {"is_staff": True}, {"is_active": False}):
        assert execute_schema(schema, UPDATE, {"id": target_id, "set": change}, user=manager).errors, change
    stored = _stored(target)
    assert (stored.email, stored.username, stored.is_staff, stored.is_active) == (
        "gated@example.com", "gated", False, True,
    )
    with actor_context(manager), pytest.raises(PermissionDenied, match="write__is_superuser"):
        User.objects.with_actor(manager).get(pk=target.pk).update_account({"is_superuser": True})
    assert not _stored(target).is_superuser

    changed = {"email": "admin-set@example.com", "username": "admin-set"}
    assert not execute_schema(schema, UPDATE, {"id": target_id, "set": changed}, user=admin).errors
    stored = _stored(target)
    assert (stored.email, stored.username) == ("admin-set@example.com", "admin-set")


def test_writers_never_update_protected_accounts_but_administrators_do(spaces_tables):
    admin = create_platform_admin("protected-admin", password=None)
    manager = _manager("protected-manager")
    other = _manager("other-manager")
    schema = _schema("console")
    for target in (admin, manager, other):
        refused = execute_schema(schema, UPDATE, {"id": target.public_id, "set": {"first_name": "X"}}, user=manager)
        assert refused.errors and "protected" in refused.errors[0].message
        assert _stored(target).first_name == ""
    for target in (admin, manager):
        patch = {"id": target.public_id, "set": {"last_name": "Y"}}
        assert not execute_schema(schema, UPDATE, patch, user=admin).errors
        assert _stored(target).last_name == "Y"


def test_update_refuses_a_stale_expected_revision(iam_admin):
    target = User.objects.create_user("stale-update")
    revision = _stored(target).account_revision
    schema = _schema("console")
    fresh = {"id": target.public_id, "set": {"first_name": "First"}, "revision": revision}
    assert not execute_schema(schema, UPDATE, fresh, user=iam_admin).errors
    stale = execute_schema(schema, UPDATE, {**fresh, "set": {"first_name": "Second"}}, user=iam_admin)
    assert stale.errors and stale.errors[0].extensions["code"] == "STALE_REVISION"
    assert _stored(target).first_name == "First"


@pytest.mark.parametrize("kind", ["self", "staff", "superuser", "platform-admin", "elevated-role"])
@pytest.mark.parametrize("verb", VERBS)
def test_every_account_verb_refuses_protected_accounts(spaces_tables, kind, verb):
    admin = create_platform_admin("verb-admin", password=None)
    target = {
        "self": lambda: admin,
        "staff": lambda: User.objects.create_user("staff-target", is_staff=True),
        "superuser": lambda: User.objects.create_user("super-target", is_superuser=True),
        "platform-admin": lambda: create_platform_admin("admin-target", password=None),
        "elevated-role": lambda: _manager("manager-target"),
    }[kind]()
    before = _stored(target)
    revision = before.account_revision
    calls = {
        "set_active": lambda user: user.set_active(False, confirmed=True, expected_revision=revision),
        "rename": lambda user: user.rename(first_name="A", last_name="B", confirmed=True, expected_revision=revision),
        "reset_password": lambda user: user.reset_password(confirmed=True, expected_revision=revision),
        "issue_password": lambda user: user.issue_password(),
    }
    with actor_context(admin), pytest.raises(ValidationError, match="protected"):
        calls[verb](User.objects.with_actor(admin).get(pk=target.pk))
    after = _stored(target)
    assert (after.is_active, after.first_name, after.password, after.account_revision) == (
        before.is_active, before.first_name, before.password, revision,
    )
    assert target.protected_against(admin)
    assert _actions(_schema("console"), admin)[target.username] == []


def test_set_user_active_round_trip_controls_sign_in(iam_admin):
    target = User.objects.create_user("seasonal", "seasonal@example.com", "sign-in-secret")
    schema = _schema("console")
    revision = _stored(target).account_revision
    variables = {"id": target.public_id, "active": False, "confirmed": False, "revision": revision}
    unconfirmed = result_data(execute_schema(schema, SET_ACTIVE, variables, user=iam_admin))["set_user_active"]
    assert unconfirmed["ok"] is False and "confirmed" in unconfirmed["validation_errors"]
    assert _stored(target).is_active

    deactivated = result_data(execute_schema(schema, SET_ACTIVE, {**variables, "confirmed": True}, user=iam_admin))
    assert deactivated["set_user_active"]["ok"] is True
    assert not _stored(target).is_active
    with system_context(reason="test.account.sign_in"):
        assert authenticate(graphql_request(AnonymousUser()), username="seasonal", password="sign-in-secret") is None
    assert not User.objects.system_context(reason="test.account.picker").active_people().filter(pk=target.pk).exists()

    stale = execute_schema(schema, SET_ACTIVE, {**variables, "active": True, "confirmed": True}, user=iam_admin)
    assert stale.errors and stale.errors[0].extensions["code"] == "STALE_REVISION"
    current = {**variables, "active": True, "confirmed": True, "revision": _stored(target).account_revision}
    assert result_data(execute_schema(schema, SET_ACTIVE, current, user=iam_admin))["set_user_active"]["ok"] is True
    with system_context(reason="test.account.sign_in"):
        assert authenticate(graphql_request(AnonymousUser()), username="seasonal", password="sign-in-secret") == target


def test_reset_user_password_returns_a_fresh_secret_with_the_username_once(iam_admin, caplog):
    target = User.objects.create_user("forgetful", "forgetful@example.com", "old-secret")
    schema = _schema("console")
    revision = _stored(target).account_revision
    variables = {"id": target.public_id, "confirmed": True, "revision": revision}

    unconfirmed = execute_schema(schema, RESET, {**variables, "confirmed": False}, user=iam_admin)
    assert unconfirmed.errors and unconfirmed.data is None
    stale = execute_schema(schema, RESET, {**variables, "revision": revision + 1}, user=iam_admin)
    assert stale.errors and stale.errors[0].extensions["code"] == "STALE_REVISION"
    assert _stored(target).check_password("old-secret")

    issued = result_data(execute_schema(schema, RESET, variables, user=iam_admin))["reset_user_password"]
    assert issued["username"] == "forgetful"
    stored = _stored(target)
    assert stored.account_revision == revision + 1
    assert stored.check_password(issued["password"]) and not stored.check_password("old-secret")
    assert issued["password"] not in stored.password and issued["password"] not in caplog.text
    replay = execute_schema(schema, RESET, variables, user=iam_admin)
    assert replay.errors and issued["password"] not in str([error.formatted for error in replay.errors])
    assert _stored(target).password == stored.password


def test_reset_needs_a_usable_password_and_issue_never_replaces_one(iam_admin):
    passwordless = User.objects.create_user("passwordless")
    with_password = User.objects.create_user("has-password", password="kept-secret")
    schema = _schema("console")
    revision = _stored(passwordless).account_revision
    reset = execute_schema(
        schema, RESET, {"id": passwordless.public_id, "confirmed": True, "revision": revision}, user=iam_admin,
    )
    assert reset.errors and "no password to reset" in reset.errors[0].message
    refused = execute_schema(schema, ISSUE, {"id": with_password.public_id}, user=iam_admin)
    assert refused.errors and "already has a usable password" in refused.errors[0].message
    assert _stored(with_password).check_password("kept-secret")

    issued = result_data(execute_schema(schema, ISSUE, {"id": passwordless.public_id}, user=iam_admin))
    assert issued["issue_user_password"]["username"] == "passwordless"
    assert _stored(passwordless).check_password(issued["issue_user_password"]["password"])


def test_rename_user_changes_only_name_fields(iam_admin):
    target = User.objects.create_user("renamed", "renamed@example.com")
    schema = _schema("console")
    revision = _stored(target).account_revision
    variables = {"id": target.public_id, "first": "  Ada ", "last": "Lovelace", "revision": revision}
    assert result_data(execute_schema(schema, RENAME, variables, user=iam_admin))["rename_user"]["ok"] is True
    stored = _stored(target)
    assert (stored.first_name, stored.last_name, stored.username, stored.email) == (
        "Ada", "Lovelace", "renamed", "renamed@example.com",
    )
    assert stored.account_revision == revision + 1
    blank = execute_schema(schema, RENAME, {**variables, "first": " ", "last": "", "revision": revision + 1},
                           user=iam_admin)
    assert result_data(blank)["rename_user"]["ok"] is False


def test_consumer_role_manages_ordinary_people_through_its_fragment(spaces_tables):
    manager = _manager("roster-manager")
    target = User.objects.create_user("managed", "managed@example.com", "managed-secret")
    schema = _schema("console")
    revision = _stored(target).account_revision
    renamed = execute_schema(schema, RENAME, {"id": target.public_id, "first": "Managed", "last": "Person",
                                              "revision": revision}, user=manager)
    assert result_data(renamed)["rename_user"]["ok"] is True
    reset = result_data(execute_schema(
        schema, RESET, {"id": target.public_id, "confirmed": True, "revision": revision + 1}, user=manager,
    ))["reset_user_password"]
    assert _stored(target).check_password(reset["password"])
    plain = User.objects.create_user("plain-viewer")
    denied = execute_schema(schema, RESET, {"id": target.public_id, "confirmed": True, "revision": revision + 2},
                            user=plain)
    assert denied.errors and _stored(target).check_password(reset["password"])


def test_account_actions_project_each_verbs_permission_and_eligibility(spaces_tables):
    admin = create_platform_admin("projection-admin", password=None)
    manager = _manager("projection-manager")
    User.objects.create_user("with-password", password="secret")
    User.objects.create_user("without-password")
    User.objects.create_user("inactive-person", is_active=False)
    User.objects.create_user("service-account", kind="service")
    User.objects.create_user("staff-person", is_staff=True)
    schema = _schema("console")

    seen_by_admin = _actions(schema, admin)
    assert seen_by_admin["with-password"] == ["set_active", "rename", "reset_password"]
    assert seen_by_admin["without-password"] == ["set_active", "rename", "issue_password"]
    assert seen_by_admin["inactive-person"] == ["set_active", "rename"]
    for protected in ("projection-admin", "projection-manager", "staff-person", "service-account"):
        assert seen_by_admin[protected] == []
    seen_by_manager = _actions(schema, manager)
    assert seen_by_manager["with-password"] == ["set_active", "rename", "reset_password"]
    assert seen_by_manager["projection-admin"] == []
    assert seen_by_manager["projection-manager"] == []
    assert _stored(manager).account_actions_for(to_subject_ref(manager)) == []
    assert _stored(admin).account_actions_for(to_subject_ref(manager)) == []


def test_account_actions_projection_query_count_stays_flat(iam_admin):
    schema = _schema("console")
    User.objects.create_user("first-row")

    def count() -> int:
        with CaptureQueriesContext(connection) as captured:
            result_data(execute_schema(schema, LIST, user=iam_admin))
        return len(captured)

    few = count()
    for index in range(6):
        User.objects.create_user(f"later-row-{index}", password="secret" if index % 2 else None)
    assert count() == few


def test_last_login_reads_through_its_field_gate(spaces_tables):
    admin = create_platform_admin("login-admin", password=None)
    manager = _manager("login-manager")
    reader = User.objects.create_user("directory-reader")
    signed_in = User.objects.create_user("signed-in")
    never = User.objects.create_user("never-signed-in")
    User._base_manager.filter(pk=signed_in.pk).update(last_login=timezone.now())
    write_relationships([
        RelationshipTuple(resource=to_object_ref(target), relation="directory_reader", subject=to_subject_ref(reader))
        for target in (signed_in, never)
    ])
    schema = _schema("console")

    def last_logins(viewer: Any) -> dict[str, Any]:
        rows = result_data(execute_schema(schema, LIST, user=viewer))["users"]
        return {row["username"]: row["last_login"] for row in rows}

    for viewer in (admin, manager):
        seen = last_logins(viewer)
        assert seen["signed-in"] is not None and seen["never-signed-in"] is None
    seen_by_reader = last_logins(reader)
    assert set(seen_by_reader) == {"signed-in", "never-signed-in"}
    assert seen_by_reader["signed-in"] is None


def test_verbs_require_their_permission_and_an_actor(iam_admin):
    target = User.objects.create_user("unmanaged", password="secret")
    plain = User.objects.create_user("plain")
    revision = _stored(target).account_revision
    with actor_context(plain), pytest.raises(PermissionDenied):
        User.objects.with_actor(plain).system_context(reason="test.target").get(pk=target.pk).with_actor(
            plain,
        ).set_active(False, confirmed=True, expected_revision=revision)
    with actor_context(iam_admin), pytest.raises(StaleRevisionError):
        target.with_actor(iam_admin).rename(
            first_name="Late", last_name="", confirmed=True, expected_revision=revision + 5,
        )
    assert _stored(target).is_active and _stored(target).first_name == ""
