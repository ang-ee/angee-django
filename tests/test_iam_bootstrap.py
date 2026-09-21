"""Tests for IAM's first-admin bootstrap command."""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any

from angee.iam.management.commands import bootstrap_admin


def test_bootstrap_admin_creates_platform_admin(monkeypatch: Any) -> None:
    """A fresh local stack gets one loginable platform admin."""

    manager = _Manager()
    _patch_command_owners(monkeypatch, manager)

    bootstrap_admin.Command().handle(username="root", email="root@example.com", password="first-secret")

    user = manager.user
    assert user is not None
    assert manager.system_reason == "iam.bootstrap_admin"
    assert user.username == "root"
    assert user.email == "root@example.com"
    assert user.is_active is True
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.password == "first-secret"
    assert manager.granted_user is user


def test_bootstrap_admin_promotes_existing_user_without_resetting_password(monkeypatch: Any) -> None:
    """Stack restarts promote the admin account without clobbering its password."""

    existing = _User(username="admin", email="", password="kept-secret")
    existing.is_active = False
    manager = _Manager(existing)
    _patch_command_owners(monkeypatch, manager)

    bootstrap_admin.Command().handle(username=None, email=None, password="generated-secret")

    assert existing.email == "admin@example.com"
    assert existing.is_active is True
    assert existing.is_staff is True
    assert existing.is_superuser is True
    assert existing.password == "kept-secret"
    assert existing.saved_update_fields == ["email", "is_active", "is_staff", "is_superuser"]
    assert manager.granted_user is existing


def _patch_command_owners(monkeypatch: Any, manager: "_Manager") -> None:
    """Patch framework owners so the command can be tested without a database."""

    _User._default_manager = manager
    monkeypatch.setattr(bootstrap_admin, "get_user_model", lambda: _User)
    monkeypatch.setattr(bootstrap_admin.transaction, "atomic", lambda *, using: nullcontext())
    monkeypatch.setattr(bootstrap_admin, "system_context", lambda *, reason: nullcontext())
    monkeypatch.setattr(
        bootstrap_admin,
        "grant_membership",
        lambda *, subject, container: setattr(manager, "granted_user", subject),
    )


class _MissingUser(Exception):
    """Fake user lookup miss."""


class _Manager:
    """Tiny stand-in for IAM's REBAC-aware user manager."""

    def __init__(self, user: "_User | None" = None) -> None:
        self.user = user
        self.system_reason: str | None = None
        self.granted_user: _User | None = None

    def db_manager(self, using: str) -> "_Manager":
        """Retain the selected command database on this manager stand-in."""

        assert using == "default"
        return self

    def system_context(self, *, reason: str) -> "_QuerySet":
        """Record the system-scope reason and return this manager."""

        self.system_reason = reason
        return _QuerySet(self)

    def get(self, **lookup: Any) -> "_User":
        """Return the existing user or raise the model's lookup miss."""

        if self.user is None:
            raise _User.DoesNotExist
        assert lookup == {"username": self.user.username}
        return self.user

    def create_superuser(self, *, username: str, email: str | None, password: str) -> "_User":
        """Create a fake superuser."""

        self.user = _User(username=username, email=email or "", password=password)
        self.user.is_staff = True
        self.user.is_superuser = True
        return self.user


class _QuerySet:
    """Tiny stand-in for the system-scoped REBAC queryset."""

    def __init__(self, manager: _Manager) -> None:
        self.manager = manager

    def get(self, **lookup: Any) -> "_User":
        """Return via the owning manager."""

        return self.manager.get(**lookup)


class _User:
    """Tiny stand-in for the composed IAM user model."""

    USERNAME_FIELD = "username"
    DoesNotExist = _MissingUser
    _default_manager = _Manager()

    def __init__(self, *, username: str, email: str, password: str | None) -> None:
        self.username = username
        self.email = email
        self.password = password
        self.is_active = True
        self.is_staff = False
        self.is_superuser = False
        self.saved_update_fields: list[str] = []

    def has_usable_password(self) -> bool:
        """Return whether this fake has a usable password."""

        return self.password is not None

    def set_password(self, password: str) -> None:
        """Set the fake password."""

        self.password = password

    def save(self, *, update_fields: list[str], using: str) -> None:
        """Record the updated fields."""

        assert using == "default"
        self.saved_update_fields = update_fields
