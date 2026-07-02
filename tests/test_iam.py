"""Tests for the built-in IAM addon contracts."""

from __future__ import annotations

import pytest
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY, get_user
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
    assert {"username", "email", "sqid", "is_staff", "is_active"} <= (field_names)
    assert "groups" not in field_names
    assert "user_permissions" not in field_names


def test_user_pk_get_is_not_the_session_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    """The session bypass is a named manager method, not a ``get`` override."""

    from angee.iam.models import User, UserManager

    manager = User._meta.managers_map["objects"]
    calls: list[tuple[str, dict[str, object]]] = []

    class ScopedQuery:
        def get(self, **kwargs: object) -> object:
            calls.append(("get", kwargs))
            return "user"

    def system_context(*, reason: str) -> ScopedQuery:
        calls.append(("system_context", {"reason": reason}))
        return ScopedQuery()

    monkeypatch.setattr(manager, "system_context", system_context)

    assert "get" not in UserManager.__dict__
    assert manager.get_for_session("usr_123") == "user"
    assert calls == [
        ("system_context", {"reason": "iam.session"}),
        ("get", {"pk": "usr_123"}),
    ]


def test_iam_model_backend_uses_named_session_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IAM auth backend owns the session reload bypass."""

    from angee.iam.auth import ModelBackend

    calls: list[object] = []

    class Manager:
        def get_for_session(self, user_id: object) -> object:
            calls.append(user_id)
            return "user"

    class UserModel:
        objects = Manager()

    monkeypatch.setattr("angee.iam.auth.get_user_model", lambda: UserModel)

    assert ModelBackend().get_user("usr_123") == "user"
    assert calls == ["usr_123"]


def test_iam_model_backend_rejects_inactive_session_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """The IAM auth backend re-applies Django's session authentication gate."""

    from angee.iam.auth import ModelBackend

    class User:
        is_active = False

    class Manager:
        def get_for_session(self, user_id: object) -> object:
            return User()

    class UserModel:
        objects = Manager()

    monkeypatch.setattr("angee.iam.auth.get_user_model", lambda: UserModel)

    assert ModelBackend().get_user("usr_123") is None


@pytest.mark.django_db
def test_inactive_session_reload_yields_anonymous_user() -> None:
    """Deactivating a user kills an already-issued session on the next reload."""

    from django.contrib.auth import get_user_model

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
def test_create_user_restores_ambient_actor_after_elevated_insert() -> None:
    """UserManager returns a scoped instance after the sudo create insert."""

    from django.contrib.auth import get_user_model

    actor = get_user_model().objects.create_user("creator")
    with actor_context(actor):
        created = get_user_model().objects.create_user("created")

    assert created.actor() == to_subject_ref(actor)
    assert not created.is_sudo()


@pytest.mark.django_db
def test_create_user_clears_insert_sudo_without_ambient_actor() -> None:
    """Actorless creates must not leak the insert sudo flag to callers."""

    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user("actorless")

    assert user.actor() is None
    assert not user.is_sudo()
