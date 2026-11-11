"""Tests for the built-in IAM addon contracts."""

from __future__ import annotations

import pytest
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    authenticate,
    get_user,
    get_user_model,
)
from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from rebac import RebacMixin, actor_context, system_context, to_subject_ref
from rebac.managers import RebacManager
from rebac.permissions_mixin import RebacPermissionsMixin


def test_user_source_model_owns_auth_identity() -> None:
    """The IAM source user composes Django auth, REBAC, and Angee IDs."""

    from angee.iam.models import User, UserManager

    field_names = {field.name for field in User._meta.get_fields()}

    assert User._meta.abstract is True
    assert issubclass(User, AbstractBaseUser)
    assert issubclass(User, RebacPermissionsMixin)
    assert issubclass(User, RebacMixin)
    manager = User._meta.managers_map["objects"]
    assert isinstance(manager, UserManager)
    assert isinstance(manager, BaseUserManager)
    assert isinstance(manager, RebacManager)
    assert User.USERNAME_FIELD == "username"
    assert {"username", "email", "sqid", "kind", "is_staff", "is_active"} <= (field_names)
    assert "groups" not in field_names
    assert "user_permissions" not in field_names


def test_user_pk_get_is_not_the_session_bypass(db: None) -> None:
    """The session bypass is a named manager method, not a ``get`` override."""

    from angee.iam.models import UserManager

    user = get_user_model().objects.create_user("session-bypass")

    assert "get" not in UserManager.__dict__
    assert get_user_model().objects.get_for_session(user.pk) == user  # no actor is in context


@pytest.mark.parametrize(
    "kind,is_active,allowed",
    [("person", True, True), ("person", False, False), ("service", True, False)],
    ids=["person", "inactive", "service"],
)
@pytest.mark.django_db
def test_iam_model_backend_uses_named_session_lookup(kind: str, is_active: bool, allowed: bool) -> None:
    """The named lookup reloads a configured user and reapplies the login gate."""

    from angee.iam.auth import ModelBackend

    user = get_user_model().objects.create_user(f"backend-{kind}-{is_active}", kind=kind, is_active=is_active)

    assert ModelBackend().get_user(user.pk) == (user if allowed else None)


@pytest.mark.django_db
def test_inactive_session_reload_yields_anonymous_user() -> None:
    """Deactivating a user kills an already-issued session on the next reload."""

    user = get_user_model().objects.create_user("session-user", password="secret")
    request = RequestFactory().get("/")
    request.session = {
        SESSION_KEY: str(user.pk),
        BACKEND_SESSION_KEY: "angee.iam.auth.ModelBackend",
        HASH_SESSION_KEY: user.get_session_auth_hash(),
    }
    user.is_active = False
    with system_context(reason="test.inactive_session.deactivate"):
        user.save(update_fields=["is_active"])

    assert isinstance(get_user(request), AnonymousUser)


@pytest.mark.django_db
def test_service_session_reload_yields_anonymous_user() -> None:
    """A service row cannot remain logged in through an already-issued session."""

    user = get_user_model().objects.create_user("session-service", password="secret")
    request = RequestFactory().get("/")
    request.session = {
        SESSION_KEY: str(user.pk),
        BACKEND_SESSION_KEY: "angee.iam.auth.ModelBackend",
        HASH_SESSION_KEY: user.get_session_auth_hash(),
    }
    user.kind = "service"
    with system_context(reason="test.service_session.kind_mutation"):
        user.save(update_fields=["kind"])

    assert isinstance(get_user(request), AnonymousUser)


@pytest.mark.django_db
def test_kind_mutation_to_service_clears_password_and_blocks_auth() -> None:
    """The User row owns service non-login semantics even after creation."""

    user = get_user_model().objects.create_user("mutated-service", password="secret")
    user.kind = "service"
    with system_context(reason="test.service_password.kind_mutation"):
        user.save(update_fields=["kind"])

    request = RequestFactory().post("/login/")
    assert authenticate(request, username="mutated-service", password="secret") is None
    with system_context(reason="test.service_password.reload"):
        user.refresh_from_db()
    assert not user.has_usable_password()


@pytest.mark.django_db
def test_overview_requires_swappable_user_role_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IAM overview requires the swapped user's queryset-owned role scope."""

    from angee.iam import roles

    class MissingPeopleScope:
        pass

    class Manager:
        def all(self) -> MissingPeopleScope:
            return MissingPeopleScope()

    class UserModel:
        _default_manager = Manager()

    monkeypatch.setattr(roles, "get_user_model", lambda: UserModel)

    with pytest.raises(AttributeError, match="without_direct_roles"):
        roles.OverviewInfo.build(peek_limit=1)


@pytest.mark.django_db
def test_create_user_restores_ambient_actor_after_elevated_insert() -> None:
    """UserManager returns a scoped instance after the sudo create insert."""

    actor = get_user_model().objects.create_user("creator")
    with actor_context(actor):
        created = get_user_model().objects.create_user("created")

    assert created.actor() == to_subject_ref(actor)
    assert not created.is_sudo()


@pytest.mark.django_db
def test_create_user_clears_insert_sudo_without_ambient_actor() -> None:
    """Actorless creates must not leak the insert sudo flag to callers."""

    user = get_user_model().objects.create_user("actorless")

    assert user.actor() is None
    assert not user.is_sudo()
