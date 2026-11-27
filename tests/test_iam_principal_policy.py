"""IAM principal policy at the Django-auth and human-authority boundaries."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.models import User as DjangoUser
from django.test import override_settings
from django.test.utils import isolate_apps
from rebac import actor_context, system_context, to_subject_ref
from rebac.roles import grant

from angee.iam.auth import ModelBackend, can_authenticate_user
from angee.iam.permissions import is_platform_admin
from angee.iam.roles import platform_admin_holders
from angee.workflows.testing.models import Workflow
from tests.conftest import create_platform_admin
from tests.iam_models import Group


@pytest.mark.parametrize(
    "kind,is_active,allowed",
    [
        ("person", True, True),
        ("person", False, False),
        ("service", True, False),
        ("service", False, False),
    ],
)
def test_login_requires_an_active_person(kind: str, is_active: bool, allowed: bool) -> None:
    user = get_user_model()(kind=kind, is_active=is_active)
    assert user.is_person is (kind == "person")
    assert can_authenticate_user(user) is allowed


@pytest.mark.parametrize("user", [object(), AnonymousUser(), DjangoUser()])
def test_login_rejects_non_iam_principals(user: object) -> None:
    """Only the configured user model can supply a login-capable principal."""

    assert can_authenticate_user(user) is False


@isolate_apps()
def test_login_uses_configured_swapped_user_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real swapped Django model supplies its own person contract."""

    class SwappedUser(AbstractBaseUser):
        @property
        def is_person(self) -> bool:
            return True

        class Meta:
            app_label = "tests"

    original_user = get_user_model()(kind="person", is_active=True)
    monkeypatch.setattr("angee.iam.auth.get_user_model", lambda: SwappedUser)

    assert can_authenticate_user(SwappedUser()) is True
    assert can_authenticate_user(original_user) is False


@override_settings(AUTHENTICATION_BACKENDS=["angee.iam.auth.ModelBackend"])
def test_login_backend_does_not_grant_django_codenames() -> None:
    """Even a superuser has no codename grants without an authorization backend."""

    user = get_user_model()(kind="person", is_active=True, is_superuser=True)
    backend = ModelBackend()
    assert backend.get_user_permissions(user) == set()
    assert backend.get_group_permissions(user) == set()
    assert backend.get_all_permissions(user) == set()
    assert not user.has_perm("iam.change_user")
    assert not user.has_module_perms("iam")


@override_settings(REBAC_UNIVERSAL_ADMIN_ROLE=None)
def test_platform_admin_never_derives_from_django_superuser() -> None:
    """Django's account flag cannot replace an explicit platform role grant."""

    user = get_user_model()(kind="person", is_active=True, is_superuser=True)
    assert is_platform_admin(user) is False


def test_platform_admin_answers_for_the_user_asked_about(db: None) -> None:
    """The answer is the argument's own membership, whoever the ambient actor is."""

    admin = create_platform_admin("argument-admin", password=None)
    plain = get_user_model().objects.create_user("argument-plain")
    with actor_context(admin):
        assert is_platform_admin(plain) is False
        assert is_platform_admin(admin) is True
    with actor_context(plain):
        assert is_platform_admin(admin) is True
        assert is_platform_admin(plain) is False
    # Outside a request (a worker, a mail bridge) no actor is bound at all.
    assert is_platform_admin(admin) is True
    with system_context(reason="test.platform_admin.argument"):
        assert is_platform_admin(admin) is True
        assert is_platform_admin(plain) is False


@pytest.mark.parametrize("user", [None, AnonymousUser()])
def test_no_signed_in_user_is_a_platform_admin(db: None, user: object) -> None:
    """Without a signed-in user there is no one to be an admin, even under an admin actor."""

    with actor_context(create_platform_admin("anonymous-ambient-admin", password=None)):
        assert is_platform_admin(user) is False


def test_platform_admin_holders_are_exactly_the_role_holders(db: None) -> None:
    """Direct and group-held members are listed; nobody else, whoever the ambient actor is."""

    direct = create_platform_admin("holders-admin", password=None)
    through_group = get_user_model().objects.create_user("holders-group-admin")
    plain = get_user_model().objects.create_user("holders-plain")
    with system_context(reason="test.platform_admin.holders"):
        group = Group.objects.create(name="Holder administrators")
        group.add_member(str(to_subject_ref(through_group)))
        grant(actor=group, role="angee/role:admin")
    holders = frozenset({str(direct.pk), str(through_group.pk)})

    assert platform_admin_holders() == holders
    with actor_context(plain):
        assert platform_admin_holders() == holders
    assert {user.pk for user in (direct, through_group, plain) if is_platform_admin(user)} == {
        direct.pk,
        through_group.pk,
    }
    with override_settings(REBAC_UNIVERSAL_ADMIN_ROLE=None):
        assert platform_admin_holders() == frozenset()


def test_workflow_service_user_tracks_identity_and_deactivates_on_delete(db: None) -> None:
    """A workflow's non-login principal survives rename and retained attribution."""
    with system_context(reason="test.workflow.principal"):
        workflow = Workflow.objects.create(key="principal-lifecycle", name="Before")
        user_id = workflow.user_id
        assert user_id is not None
        workflow.name = "After"
        workflow.save(update_fields={"name"})
        user = get_user_model()._base_manager.get(pk=user_id)
        assert (user.username, user.first_name, user.kind) == (f"workflow-{workflow.sqid}", "After", "service")
        assert not user.has_usable_password()
        Workflow.objects.filter(pk=workflow.pk).delete()
        user.refresh_from_db()
        assert not user.is_active
