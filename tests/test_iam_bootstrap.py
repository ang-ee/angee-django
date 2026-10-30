"""Initial-admin persistence belongs to IAM's manager; the command dispatches."""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from rebac import ObjectRef
from rebac.memberships import members_of

from angee.iam import models
from angee.iam.autoconfig import bootstrap_admin_settings
from angee.iam.management.commands import bootstrap_admin


def test_bootstrap_admin_creates_platform_admin(composed_tables) -> None:
    """The manager itself creates a loginable account and its role membership."""

    user, created = get_user_model().objects.ensure_bootstrap_admin(
        "root", email="ROOT@EXAMPLE.COM", password="first-secret",
    )

    assert created
    assert user.username == "root"
    assert user.email == "root@example.com"
    assert user.is_active and user.is_staff and user.is_superuser
    assert user.password != "first-secret" and user.check_password("first-secret")
    assert [member.subject_id for member in members_of(ObjectRef("angee/role", "admin"))] == [str(user.pk)]


@pytest.mark.parametrize("existing_password", ["kept-secret", None])
def test_bootstrap_admin_promotes_existing_user_without_resetting_password(composed_tables, existing_password) -> None:
    """The command promotes an account and only fills an unusable password."""

    User = get_user_model()
    existing = User.objects.create_user("admin", is_active=False, password=existing_password)
    original_hash = existing.password
    output = StringIO()
    command = bootstrap_admin.Command(stdout=output)
    command.handle(database="default", username="admin", email="admin@example.com", password="generated-secret")

    stored = User._base_manager.get(pk=existing.pk)
    assert stored.email == "admin@example.com"
    assert stored.is_active and stored.is_staff and stored.is_superuser
    assert stored.check_password(existing_password or "generated-secret")
    if existing_password:
        assert stored.password == original_hash
    assert "ensured 'admin'" in output.getvalue()
    assert "generated-secret" not in output.getvalue()


@pytest.mark.parametrize("existing", [False, True])
def test_bootstrap_admin_membership_failure_rolls_back_account_changes(composed_tables, monkeypatch, existing) -> None:
    """A failed role grant cannot leave a newly created or promoted admin behind."""

    User = get_user_model()
    if existing:
        User.objects.create_user("rollback", is_active=False)

    def fail(**kwargs):
        raise RuntimeError("membership failed")

    monkeypatch.setattr(models, "grant_membership", fail)
    with pytest.raises(RuntimeError, match="membership failed"):
        User.objects.ensure_bootstrap_admin("rollback", email="rollback@example.com", password="secret")
    if existing:
        user = User._base_manager.get(username="rollback")
        assert not user.is_active and not user.is_staff and not user.is_superuser
        assert not user.has_usable_password() and not user.email
    else:
        assert not User._base_manager.filter(username="rollback").exists()


def test_bootstrap_admin_requires_password_for_new_user(composed_tables, monkeypatch) -> None:
    """A manager refusal becomes a command error without creating an account."""

    monkeypatch.setattr(
        bootstrap_admin, "bootstrap_admin_settings", lambda: dict.fromkeys(("username", "email", "password")),
    )
    with pytest.raises(CommandError, match="needs ANGEE_BOOTSTRAP_ADMIN_PASSWORD"):
        bootstrap_admin.Command().handle(database="default", username="missing", email=None, password=None)
    assert not get_user_model()._base_manager.filter(username="missing").exists()


@pytest.mark.parametrize("field", ["username", "email", "password"])
def test_bootstrap_admin_settings_precedence_and_literal_values(settings, monkeypatch, field) -> None:
    """Both callers read the same explicit values without altering a secret."""

    name = f"ANGEE_BOOTSTRAP_ADMIN_{field.upper()}"
    fallback = f"DJANGO_SUPERUSER_{field.upper()}"
    setattr(settings, name, None)
    monkeypatch.setenv(fallback, "django-value")
    monkeypatch.setenv(name, "angee-value")
    assert bootstrap_admin_settings()[field] == "angee-value"
    setattr(settings, name, "settings-value")
    assert bootstrap_admin_settings()[field] == "settings-value"
    setattr(settings, name, "")
    monkeypatch.setenv(name, "")
    assert bootstrap_admin_settings()[field] == "django-value"
    monkeypatch.setenv(fallback, "$literal-value")
    assert bootstrap_admin_settings()[field] == "$literal-value"
    monkeypatch.delenv(fallback)
    assert bootstrap_admin_settings()[field] is None
