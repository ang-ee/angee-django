"""Actor-gated creation, transactional parties linkage, and credential issue."""

import logging

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, actor_context, current_actor, system_context, to_subject_ref
from rebac.errors import MissingActorError

from angee.iam.events import person_created
from angee.iam.models import AccountExists
from tests.iam_campaign import Person
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_campaign import person_events as person_events

User = get_user_model()


@pytest.mark.parametrize("password", [None, "a-secret-for-this-test"])
def test_create_person_fixes_shape_hashes_password_and_emits_inside_transaction(iam_admin, person_events, password):
    with actor_context(iam_admin):
        user = User.objects.create_person(
            "new-person",
            "  New.Person@EXAMPLE.COM ",
            password,
            first_name="New",
            last_name="Person",
        )

    stored = User._base_manager.get(pk=user.pk)
    assert (stored.kind, stored.is_active, stored.is_staff, stored.is_superuser) == ("person", True, False, False)
    assert (stored.email, stored.first_name, stored.last_name) == ("new.person@example.com", "New", "Person")
    assert stored.has_usable_password() is (password is not None)
    if password is not None:
        assert stored.check_password(password)
        assert stored.password != password
    assert user.actor() == to_subject_ref(iam_admin)
    assert not user.is_sudo()
    assert person_events == [{"sender": User, "user": user, "atomic": True}]
    person = Person._base_manager.get(user=user)
    assert person.created_by_id == user.pk
    person_created.send(sender=User, instance=user)
    assert Person._base_manager.filter(user=user).count() == 1


def test_create_person_requires_an_authorized_actor_and_writes_nothing(iam_admin, person_events):
    actor = User.objects.create_user("plain")
    before = User._base_manager.count(), Person._base_manager.count()
    with actor_context(actor), pytest.raises(PermissionDenied):
        User.objects.create_person("denied", "denied@example.com")
    assert current_actor() is None
    with pytest.raises(MissingActorError):
        User.objects.create_person("actorless", "actorless@example.com")
    assert (User._base_manager.count(), Person._base_manager.count()) == before
    assert person_events == []


@pytest.mark.parametrize(
    "field,value", [("kind", "service"), ("is_staff", True), ("is_superuser", True), ("is_active", False)]
)
def test_create_person_does_not_accept_authority_overrides(iam_admin, field, value):
    with actor_context(iam_admin), pytest.raises(TypeError, match="unexpected keyword"):
        User.objects.create_person("overridden", **{field: value})
    assert not User._base_manager.filter(username="overridden").exists()


@pytest.mark.parametrize("values", [{"username": "bad name"}, {"username": "valid", "email": "not-an-email"}])
def test_create_person_validates_before_emitting_or_linking(iam_admin, person_events, values):
    before = User._base_manager.count(), Person._base_manager.count()
    with actor_context(iam_admin), pytest.raises(ValidationError):
        User.objects.create_person(**values)
    assert (User._base_manager.count(), Person._base_manager.count()) == before
    assert person_events == []


@pytest.mark.parametrize("system", [False, True])
def test_receiver_failure_rolls_back_account_and_parties_link(iam_admin, person_events, system):
    before = User._base_manager.count(), Person._base_manager.count()

    def fail(sender, instance, **kwargs):
        assert Person._base_manager.filter(user=instance).exists()
        raise RuntimeError("receiver failed")

    person_created.connect(fail, weak=False)
    try:
        with pytest.raises(RuntimeError, match="receiver failed"):
            if system:
                User.objects.create_person_as_system("rollback", "rollback@example.com", reason="test.ingress")
            else:
                with actor_context(iam_admin):
                    User.objects.create_person("rollback", "rollback@example.com")
    finally:
        person_created.disconnect(fail)
    assert (User._base_manager.count(), Person._base_manager.count()) == before
    assert len(person_events) == 1
    assert person_events[0]["atomic"]


def test_outer_transaction_rollback_removes_account_and_person(iam_admin):
    with actor_context(iam_admin), pytest.raises(RuntimeError, match="outer failed"), transaction.atomic():
        user = User.objects.create_person("outer", "outer@example.com")
        assert Person._base_manager.filter(user=user).exists()
        raise RuntimeError("outer failed")
    assert not User._base_manager.filter(username="outer").exists()
    assert not Person._base_manager.filter(user_id=user.pk).exists()


def test_system_creation_has_no_actor_logs_named_reason_and_emits_once(iam_admin, person_events, caplog):
    assert current_actor() is None
    with caplog.at_level(logging.INFO, logger="angee.iam.models"):
        user = User.objects.create_person_as_system("ingress", " INGRESS@example.com ", reason="test.channel.capture")
    assert user.actor() is None
    assert not user.is_sudo()
    assert (user.kind, user.is_active, user.is_staff, user.is_superuser) == ("person", True, False, False)
    assert not User._base_manager.get(pk=user.pk).has_usable_password()
    assert person_events == [{"sender": User, "user": user, "atomic": True}]
    assert Person._base_manager.filter(user=user).count() == 1
    assert "test.channel.capture" in caplog.text
    with pytest.raises(AccountExists, match="^ACCOUNT_EXISTS$"):
        User.objects.create_person_as_system("duplicate", user.email, reason="test.channel.capture")
    assert len(person_events) == 1


@pytest.mark.parametrize("reason", ["", " \t\n"])
def test_system_creation_requires_a_named_reason(iam_admin, person_events, reason):
    with pytest.raises(ValueError, match="named reason"):
        User.objects.create_person_as_system("unnamed", "unnamed@example.com", reason=reason)
    assert not User._base_manager.filter(username="unnamed").exists()
    assert person_events == []


@pytest.mark.parametrize("email", ["", " \t\n", "invalid"])
def test_system_creation_requires_a_valid_nonempty_email(iam_admin, person_events, email):
    with pytest.raises(ValidationError):
        User.objects.create_person_as_system("invalid-system", email, reason="test.ingress")
    assert not User._base_manager.filter(username="invalid-system").exists()
    assert person_events == []


def test_issue_password_requires_permission_and_an_actor(iam_admin):
    target = User.objects.create_user("passwordless")
    plain = User.objects.create_user("plain")
    with pytest.raises(MissingActorError):
        target.issue_password()
    with actor_context(plain), pytest.raises(PermissionDenied):
        target.with_actor(plain).issue_password()
    assert not User._base_manager.get(pk=target.pk).has_usable_password()


@pytest.mark.parametrize(
    "attrs,refusal",
    [
        ({"is_active": False}, "Only active accounts"),
        ({"is_staff": True}, "protected"),
        ({"is_superuser": True}, "protected"),
        ({"kind": "service"}, "Only person accounts"),
    ],
)
def test_issue_password_refuses_ineligible_accounts(iam_admin, attrs, refusal):
    target = User.objects.create_user("ineligible", **attrs)
    before = target.password
    with actor_context(iam_admin), pytest.raises(ValidationError, match=refusal):
        target.with_actor(iam_admin).issue_password()
    assert User._base_manager.get(pk=target.pk).password == before


def test_issue_password_returns_once_stores_only_hash_and_refuses_stale_replay(iam_admin, caplog):
    target = User.objects.create_user("passwordless")
    stale = User._base_manager.get(pk=target.pk)
    with actor_context(iam_admin):
        password = target.with_actor(iam_admin).issue_password()
        assert password
        stored = User._base_manager.get(pk=target.pk)
        assert stored.password != password
        assert stored.check_password(password)
        with pytest.raises(ValidationError, match="already has a usable password"):
            stale.with_actor(iam_admin).issue_password()
    assert User._base_manager.get(pk=target.pk).password == stored.password
    assert password not in caplog.text


def test_trusted_create_user_does_not_emit_person_created(iam_admin, person_events):
    with system_context(reason="test.bootstrap"):
        user = User.objects.create_user("bootstrap")
    assert person_events == []
    assert not Person._base_manager.filter(user=user).exists()
