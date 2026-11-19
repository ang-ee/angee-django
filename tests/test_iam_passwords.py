"""Self-service password changes and the existing administrative password update."""

import pytest
from django.contrib.auth import HASH_SESSION_KEY, get_user, get_user_model, login
from django.contrib.auth.models import AnonymousUser
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref

from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from tests.conftest import create_platform_admin
from tests.test_iam_account_actions import UPDATE, _manager
from tests.test_iam_graphql import _schema

User = get_user_model()
CURRENT = "Old-password-unique-731!"
NEW = "New-password-unique-942!"
CHANGE = """mutation($current: String!, $new: String!) {
  change_password(current_password: $current, new_password: $new) { ok message validation_errors }
}"""


@pytest.fixture
def password_user(db, settings):
    settings.AUTH_PASSWORD_VALIDATORS = [
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
        {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
        {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    ]
    return User.objects.create_user("password-user", password=CURRENT)


@pytest.mark.parametrize(("current", "new", "field"), [
    ("wrong-password", NEW, "current_password"),
    (CURRENT, "short", "new_password"),
    (CURRENT, "123456789012", "new_password"),
    (CURRENT, CURRENT, "new_password"),
])
def test_change_password_refusals_are_field_keyed_and_do_not_write(password_user, current, new, field, caplog):
    before = User._base_manager.get(pk=password_user.pk)
    result = result_data(execute_schema(
        _schema("public"), CHANGE, {"current": current, "new": new}, user=password_user, session=True,
    ))["change_password"]
    assert not result["ok"] and field in result["validation_errors"]
    stored = User._base_manager.get(pk=password_user.pk)
    assert (stored.password, stored.account_revision) == (before.password, before.account_revision)
    assert CURRENT not in str(result) + caplog.text and NEW not in str(result) + caplog.text


@pytest.mark.parametrize("schema_name", ["public", "console"])
def test_change_password_keeps_only_the_current_session(password_user, schema_name, caplog):
    request = graphql_request(password_user, session=True)
    other_session = graphql_request(password_user, session=True)
    with system_context(reason="test.password.sessions"):
        for signed_in in (request, other_session):
            login(signed_in, password_user, backend="angee.iam.auth.ModelBackend")
            signed_in.session.save()
    old_key, old_hash = request.session.session_key, request.session[HASH_SESSION_KEY]
    another = User.objects.create_user("another-password-user", password=CURRENT)
    another_hash = another.password
    revision = User._base_manager.get(pk=password_user.pk).account_revision

    result = result_data(execute_schema(
        _schema(schema_name), CHANGE, {"current": CURRENT, "new": NEW}, request=request,
    ))["change_password"]
    assert result["ok"] and result["message"] == "Password changed."
    stored = User._base_manager.get(pk=password_user.pk)
    assert stored.check_password(NEW) and not stored.check_password(CURRENT)
    assert stored.account_revision == revision + 1
    assert stored.password != NEW and NEW not in caplog.text + str(result)
    assert request.session.session_key != old_key
    assert request.session[HASH_SESSION_KEY] == stored.get_session_auth_hash() != old_hash
    with system_context(reason="test.password.session_read"):
        assert get_user(request).pk == password_user.pk
        assert not get_user(other_session).is_authenticated
    assert User._base_manager.get(pk=another.pk).password == another_hash


def test_change_password_cannot_address_another_account(password_user):
    other = User.objects.create_user("other-account", password=CURRENT)
    with actor_context(password_user), pytest.raises(PermissionDenied, match="only your own"):
        other.with_actor(password_user).change_password(current_password=CURRENT, new_password=NEW)
    result = execute_schema(
        _schema("public"),
        """mutation($id: ID!, $current: String!, $new: String!) {
          change_password(id: $id, current_password: $current, new_password: $new) { ok }
        }""",
        {"id": other.public_id, "current": CURRENT, "new": NEW}, user=password_user, session=True,
    )
    assert result.errors and "Unknown argument 'id'" in result.errors[0].message
    assert User._base_manager.get(pk=other.pk).check_password(CURRENT)


def test_change_password_requires_sign_in_and_a_usable_password(password_user):
    anonymous = execute_schema(
        _schema("public"), CHANGE, {"current": CURRENT, "new": NEW}, user=AnonymousUser(), session=True,
    )
    assert anonymous.errors and anonymous.errors[0].extensions["code"] == "PERMISSION_DENIED"
    passwordless = User.objects.create_user("link-only")
    refused = result_data(execute_schema(
        _schema("public"), CHANGE, {"current": CURRENT, "new": NEW}, user=passwordless, session=True,
    ))["change_password"]
    assert not refused["ok"] and "no password" in refused["validation_errors"]["current_password"][0]
    assert not User._base_manager.get(pk=passwordless.pk).has_usable_password()


def test_set_password_uses_existing_gates_for_ordinary_protected_and_self(password_user, caplog):
    admin = create_platform_admin("set-password-admin", password=CURRENT)
    manager = _manager("set-password-manager")
    protected = User.objects.create_user("protected-password", password=CURRENT, is_staff=True)
    schema = _schema("console")
    for target in (password_user, protected, admin, manager):
        assert "set_password" in target.account_actions_for(to_subject_ref(admin))
        assert "set_password" not in target.account_actions_for(to_subject_ref(manager))
        assert "set_password" not in target.account_actions_for(to_subject_ref(password_user))
        before = User._base_manager.get(pk=target.pk)
        variables = {"id": target.public_id, "set": {"password": NEW}, "revision": before.account_revision}
        assert execute_schema(schema, UPDATE, variables, user=manager).errors
        assert User._base_manager.get(pk=target.pk).password == before.password
        result = result_data(execute_schema(schema, UPDATE, variables, user=admin))
        assert result["update_users_by_pk"]["revision"] == before.account_revision + 1
        assert User._base_manager.get(pk=target.pk).check_password(NEW)
        assert NEW not in str(result) + caplog.text
        stale = execute_schema(schema, UPDATE, variables, user=admin)
        assert stale.errors and stale.errors[0].extensions["code"] == "STALE_REVISION"


def test_set_password_validates_at_the_existing_update_owner(password_user):
    admin = create_platform_admin("validator-admin", password=None)
    before = User._base_manager.get(pk=password_user.pk)
    result = execute_schema(_schema("console"), UPDATE, {
        "id": password_user.public_id, "set": {"password": "short"}, "revision": before.account_revision,
    }, user=admin)
    assert result.errors and "password" in result.errors[0].extensions["validationErrors"]
    assert User._base_manager.get(pk=password_user.pk).password == before.password
