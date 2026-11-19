"""Self-service password changes and the administrative account verb."""

import pytest
from axes.models import AccessAttempt
from django.contrib.auth import HASH_SESSION_KEY, get_user, get_user_model, login
from django.contrib.auth.hashers import PBKDF2PasswordHasher, get_hasher
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.password_validation import MinimumLengthValidator, get_default_password_validators
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref

from angee.graphql.testing.documents import execute_schema, graphql_request, result_data
from angee.graphql.view_as import ViewAs
from angee.iam.auth import ONE_ATTEMPT_MESSAGE
from angee.iam.autoconfig import SETTINGS as IAM_SETTINGS
from angee.iam.models import SignInLinkSpent
from tests.conftest import create_platform_admin
from tests.iam_models import SignInLink
from tests.test_iam_account_actions import _actions, _manager
from tests.test_iam_graphql import _schema

User = get_user_model()
CURRENT = "Old-password-unique-731!"
NEW = "New-password-unique-942!"
CHANGE = """mutation($current: String!, $new: String!) {
  change_password(current_password: $current, new_password: $new) { ok message validation_errors }
}"""
SET = """mutation($id: ID!, $password: String!, $revision: Int!) {
  set_user_password(id: $id, password: $password, expected_revision: $revision) { ok message code validation_errors }
}"""
CREATE = """mutation($object: users_insert_input!) {
  insert_users_one(object: $object) { id username }
}"""
LOGIN = """mutation($name: String!, $password: String!) {
  login(username: $name, password: $password) { ok }
}"""


class CountingPasswordHasher(PBKDF2PasswordHasher):
    """Observe Django's real hashing and verification through PASSWORD_HASHERS."""

    def __init__(self):
        self.encode_calls = 0
        self.verify_calls = 0

    def encode(self, password, salt, iterations=None):
        self.encode_calls += 1
        return super().encode(password, salt, iterations)

    def verify(self, password, encoded):
        self.verify_calls += 1
        return super().verify(password, encoded)


class CountingPasswordValidator(MinimumLengthValidator):
    """Observe validation through AUTH_PASSWORD_VALIDATORS without changing its rule."""

    def __init__(self, min_length=12):
        super().__init__(min_length)
        self.calls = 0

    def validate(self, password, user=None):
        self.calls += 1
        return super().validate(password, user)


@pytest.fixture
def password_checks(password_user, settings):
    settings.PASSWORD_HASHERS = [f"{__name__}.CountingPasswordHasher"]
    settings.AUTH_PASSWORD_VALIDATORS = [{"NAME": f"{__name__}.CountingPasswordValidator"}]
    return get_hasher(), get_default_password_validators()[0]


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
        other.with_actor(password_user).change_password(
            graphql_request(password_user), current_password=CURRENT, new_password=NEW,
        )
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


def test_set_password_admits_other_people_including_protected_accounts(password_user, caplog, password_checks):
    hasher, validator = password_checks
    admin = create_platform_admin("set-password-admin", password=CURRENT)
    manager = _manager("set-password-manager")
    peer = create_platform_admin("peer-admin", password=CURRENT)
    allowed_targets = (
        password_user, manager, peer,
        User.objects.create_user("staff-password", password=CURRENT, is_staff=True),
        User.objects.create_user("super-password", password=CURRENT, is_superuser=True),
        User.objects.create_user("inactive-password", password=CURRENT, is_active=False),
    )
    service = User.objects.create_user("service-password", kind="service")
    schema = _schema("console")

    def variables(target, revision):
        return {"id": target.public_id, "revision": revision, "password": NEW}

    offered = _actions(schema, admin)
    for target in allowed_targets:
        before = User._base_manager.get(pk=target.pk)
        assert "set_password" in offered[target.username]
        result = result_data(execute_schema(schema, SET, variables(target, before.account_revision), user=admin))
        assert result["set_user_password"]["ok"]
        stored = User._base_manager.get(pk=target.pk)
        assert stored.check_password(NEW)
        assert stored.account_revision == before.account_revision + 1
        stale = execute_schema(schema, SET, variables(target, before.account_revision), user=admin)
        assert stale.errors and stale.errors[0].extensions["code"] == "STALE_REVISION"
        assert NEW not in str(result) + str(stale) + caplog.text

    assert validator.calls == len(allowed_targets) and hasher.encode_calls > 0
    calls = hasher.encode_calls, hasher.verify_calls, validator.calls
    assert peer.with_actor(manager).has_access("write")
    assert not peer.with_actor(manager).has_access("administer")
    for actor, target in ((admin, admin), (admin, service), (manager, peer), (manager, password_user),
                          (password_user, password_user)):
        assert "set_password" not in _actions(schema, actor)[target.username]
        before = User._base_manager.get(pk=target.pk)
        refused = result_data(execute_schema(schema, SET, variables(target, before.account_revision), user=actor))
        assert refused["set_user_password"]["ok"] is False
        if actor == target == admin:
            message = refused["set_user_password"]["message"]
            assert "Change password" in message and "current password" in message and "keeps this session" in message
        assert User._base_manager.get(pk=target.pk).password == before.password
        assert User._base_manager.get(pk=target.pk).account_revision == before.account_revision
        assert NEW not in str(refused) + caplog.text
    assert (hasher.encode_calls, hasher.verify_calls, validator.calls) == calls


def test_set_password_keeps_actorless_protected_account_refusal(password_user):
    target = create_platform_admin("system-protected-password", password=CURRENT)
    with system_context(reason="test.password.actorless"), pytest.raises(ValidationError, match="protected"):
        target.sudo(reason="test.password.actorless").set_account_password(
            NEW, expected_revision=target.account_revision,
        )
    assert User._base_manager.get(pk=target.pk).check_password(CURRENT)


def test_set_password_ends_target_sessions_and_retires_sign_in_links(password_user, caplog):
    admin = create_platform_admin("session-password-admin", password=CURRENT)
    requests = [graphql_request(user, session=True) for user in (admin, password_user, password_user)]
    with system_context(reason="test.password.sessions"):
        for request in requests:
            login(request, request.user, backend="angee.iam.auth.ModelBackend")
            request.session.save()
        with transaction.atomic():
            locked = User.objects.lock_if_supported().get(pk=password_user.pk)
            link, token = SignInLink.objects.issue(locked, actor=to_subject_ref(admin))
            link.sent_at = timezone.now()
            link.save(update_fields=["sent_at"])
            assert link.account_hash == SignInLink.account_digest(locked)
    result = result_data(execute_schema(_schema("console"), SET, {
        "id": password_user.public_id, "password": NEW, "revision": locked.account_revision,
    }, request=requests[0]))
    assert result["set_user_password"]["ok"]
    with system_context(reason="test.password.sessions.read"):
        assert get_user(requests[0]).pk == admin.pk
        assert all(not get_user(request).is_authenticated for request in requests[1:])
        stored = User._base_manager.get(pk=password_user.pk)
        assert link.account_hash != SignInLink.account_digest(stored)
        with pytest.raises(SignInLinkSpent):
            SignInLink.objects.redeem(token)
    assert all(secret not in str(result) + caplog.text for secret in (NEW, token))


@pytest.mark.parametrize("protected", [False, True])
def test_raw_user_update_no_longer_accepts_password(password_user, protected, caplog):
    admin = create_platform_admin("raw-update-admin", password=None)
    target = User.objects.create_user("raw-update-target", password=CURRENT, is_staff=protected)
    before = User._base_manager.get(pk=target.pk)
    # The removed input field is rejected during document validation, before any
    # variables or metadata updates are evaluated.
    result = execute_schema(_schema("console"), """mutation($id: String!, $password: String!) {
      update_users_by_pk(pk_columns: {id: $id}, _set: {
        first_name: "Changed", is_staff: false, password: $password
      }) { id }
    }""", {"id": target.public_id, "password": NEW}, user=admin)
    assert result.errors
    assert any("unknown field 'password'" in error.message and "'users_set_input'" in error.message
               for error in result.errors)
    assert result.data is None
    stored = User._base_manager.get(pk=target.pk)
    assert (stored.first_name, stored.is_staff, stored.password, stored.account_revision) == (
        before.first_name, before.is_staff, before.password, before.account_revision,
    )
    assert NEW not in str(result) + caplog.text


@pytest.mark.parametrize("password", ["short", "123456789012", ""])
def test_actor_typed_creation_set_and_change_share_validation(password_user, password):
    admin = create_platform_admin("typed-password-admin", password=None)
    schema = _schema("console")
    created = execute_schema(schema, CREATE, {"object": {"username": "typed-person", "password": password}}, user=admin)
    assert created.errors and "password" in created.errors[0].extensions["validationErrors"]
    assert not User._base_manager.filter(username="typed-person").exists()
    result = result_data(execute_schema(schema, SET, {
        "id": password_user.public_id, "password": password, "revision": password_user.account_revision,
    }, user=admin))["set_user_password"]
    assert not result["ok"] and "password" in result["validation_errors"]
    changed = result_data(execute_schema(schema, CHANGE, {"current": CURRENT, "new": password},
                                         user=password_user, session=True))["change_password"]
    assert not changed["ok"] and "new_password" in changed["validation_errors"]
    assert User._base_manager.get(pk=password_user.pk).check_password(CURRENT)


def test_creation_validates_only_actor_typed_passwords(password_user, password_checks):
    hasher, validator = password_checks
    admin = create_platform_admin("create-password-admin", password=None)
    schema = _schema("console")
    created = result_data(execute_schema(schema, CREATE, {
        "object": {"username": "typed-person", "password": NEW},
    }, user=admin))["insert_users_one"]
    assert User._base_manager.get(username=created["username"]).check_password(NEW)
    # Install/fixture paths deliberately keep Django's trusted factory semantics.
    assert User.objects.create_user("seeded", password="weak").check_password("weak")
    assert User.objects.create_superuser("seeded-admin", password="weak").check_password("weak")
    ingress = User.objects.create_person_as_system("ingress", "ingress@example.com", reason="test.ingress")
    assert not ingress.has_usable_password()

    assert validator.calls == 1 and hasher.encode_calls > 0
    calls = hasher.encode_calls, hasher.verify_calls, validator.calls
    denied = execute_schema(schema, CREATE, {
        "object": {"username": "denied", "password": "short"},
    }, user=password_user)
    assert denied.errors and denied.errors[0].extensions["code"] == "PERMISSION_DENIED"
    assert not User._base_manager.filter(username="denied").exists()
    assert (hasher.encode_calls, hasher.verify_calls, validator.calls) == calls


@pytest.mark.parametrize("order", [("login", "change"), ("change", "login")])
def test_login_and_change_password_share_lockout_without_hashing_when_locked_out(
    password_user, settings, caplog, password_checks, order,
):
    hasher, validator = password_checks
    settings.AUTHENTICATION_BACKENDS = IAM_SETTINGS["AUTHENTICATION_BACKENDS:append"]
    settings.AXES_FAILURE_LIMIT = 2
    settings.AXES_LOCKOUT_PARAMETERS = ["username"]
    schema = _schema("public")
    before = User._base_manager.get(pk=password_user.pk)
    results = []
    for count, operation in enumerate(order, start=1):
        if operation == "login":
            result = result_data(execute_schema(schema, LOGIN, {
                "name": password_user.username, "password": "wrong-login",
            }, user=AnonymousUser(), session=True))["login"]
        else:
            result = result_data(execute_schema(schema, CHANGE, {
                "current": "wrong-current", "new": NEW,
            }, user=password_user, session=True))["change_password"]
            assert "current_password" in result["validation_errors"]
        assert result["ok"] is False
        results.append(result)
        # Both operations contribute to exactly the same username counter.
        assert AccessAttempt.objects.get(username=password_user.username).failures_since_start == count

    assert hasher.verify_calls > 0 and validator.calls == 0
    calls = hasher.encode_calls, hasher.verify_calls, validator.calls
    refused_login = result_data(execute_schema(schema, LOGIN, {
        "name": password_user.username, "password": CURRENT,
    }, user=AnonymousUser(), session=True))["login"]
    assert refused_login["ok"] is False
    refused_change = result_data(execute_schema(schema, CHANGE, {
        "current": CURRENT, "new": NEW,
    }, user=password_user, session=True))["change_password"]
    assert refused_change["ok"] is False and "current_password" in refused_change["validation_errors"]
    stored = User._base_manager.get(pk=password_user.pk)
    assert (stored.password, stored.account_revision) == (before.password, before.account_revision)
    assert (hasher.encode_calls, hasher.verify_calls, validator.calls) == calls
    recorded = str(list(AccessAttempt.objects.values())) + caplog.text + str([*results, refused_login, refused_change])
    assert all(secret not in recorded for secret in (CURRENT, NEW, "wrong-login", "wrong-current"))


def test_one_password_attempt_per_request_including_aliases_and_login(password_user, settings):
    settings.AUTHENTICATION_BACKENDS = IAM_SETTINGS["AUTHENTICATION_BACKENDS:append"]
    request = graphql_request(password_user, session=True)
    result = execute_schema(_schema("public"), """mutation($wrong: String!, $right: String!, $new: String!) {
      first: change_password(current_password: $wrong, new_password: $new) { ok }
      second: change_password(current_password: $right, new_password: $new) { ok }
    }""", {"wrong": "wrong", "right": CURRENT, "new": NEW}, request=request)
    assert result.errors and result.errors[0].message == ONE_ATTEMPT_MESSAGE
    assert AccessAttempt.objects.get(username=password_user.username).failures_since_start == 1
    again = execute_schema(_schema("public"), LOGIN,
                           {"name": password_user.username, "password": CURRENT}, request=request)
    assert again.errors and again.errors[0].message == ONE_ATTEMPT_MESSAGE
    assert User._base_manager.get(pk=password_user.pk).check_password(CURRENT)


def test_authentication_must_return_the_actor_and_match_the_locked_hash(password_user, monkeypatch):
    another = User.objects.create_user("wrong-authenticated-user", password=CURRENT)
    monkeypatch.setattr("angee.iam.auth.authenticate_once", lambda *args, **kwargs: another)
    refused = result_data(execute_schema(_schema("public"), CHANGE, {"current": CURRENT, "new": NEW},
                                         user=password_user, session=True))["change_password"]
    assert not refused["ok"]
    authenticated = User._base_manager.get(pk=password_user.pk)
    # Another request changed the row between authentication and the lock.
    User._base_manager.filter(pk=password_user.pk).update(password=another.password)
    monkeypatch.setattr("angee.iam.auth.authenticate_once", lambda *args, **kwargs: authenticated)
    refused = result_data(execute_schema(_schema("public"), CHANGE, {"current": CURRENT, "new": NEW},
                                         user=password_user, session=True))["change_password"]
    assert not refused["ok"] and "current_password" in refused["validation_errors"]
    assert User._base_manager.get(pk=password_user.pk).password == another.password


def test_session_user_projects_only_the_usable_password_fact(password_user):
    passwordless = User.objects.create_user("oidc-only")
    for user, usable in ((password_user, True), (passwordless, False)):
        row = result_data(execute_schema(_schema("public"), "{ current_user { id has_usable_password } }", user=user))
        assert row["current_user"]["has_usable_password"] is usable
        assert CURRENT not in str(row) and user.password not in str(row)


@pytest.mark.parametrize("schema_name", ["public", "console"])
def test_view_as_hides_password_usability_and_refuses_password_changes(password_user, schema_name, monkeypatch):
    admin = create_platform_admin("password-preview-admin", password=None)
    request = graphql_request(password_user, session=True)
    request.view_as = ViewAs(real_user=admin, target=password_user)
    schema = _schema(schema_name)
    row = result_data(execute_schema(schema, "{ current_user { id has_usable_password } }", request=request))
    assert row["current_user"] == {"id": password_user.public_id, "has_usable_password": None}

    def unexpected(*args, **kwargs):
        pytest.fail("Preview must refuse the mutation before authentication")

    monkeypatch.setattr("angee.iam.auth.authenticate_once", unexpected)
    before = User._base_manager.get(pk=password_user.pk)
    refused = execute_schema(schema, CHANGE, {"current": CURRENT, "new": NEW}, request=request)
    assert refused.errors and refused.errors[0].extensions["code"] == "VIEW_AS_READ_ONLY"
    assert refused.data is None
    stored = User._base_manager.get(pk=password_user.pk)
    assert (stored.password, stored.account_revision) == (before.password, before.account_revision)
    assert CURRENT not in str(refused) and NEW not in str(refused)
