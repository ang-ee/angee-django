"""Named capabilities: the permissions on ``iam/capability:main`` the session's identity holds."""

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rebac import system_context, to_subject_ref
from rebac.roles import grant as grant_role

from angee.iam.roles import declared_capabilities, subject_capabilities
from tests.conftest import create_platform_admin, execute_schema, result_data
from tests.iam_models import Group
from tests.test_iam_account_actions import _manager
from tests.test_iam_graphql import _schema
from tests.test_spaces import spaces_tables as spaces_tables

User = get_user_model()
IDENTITY = "{ current_user { username capabilities } declared_capabilities }"


def _identity(user: Any) -> dict[str, Any]:
    return result_data(execute_schema(_schema("public"), IDENTITY, user=user))


def _capabilities(user: Any) -> list[str]:
    return _identity(user)["current_user"]["capabilities"]


def test_capabilities_follow_direct_grants_and_every_derived_path(spaces_tables):
    roster_manager = _manager("capable-roster")
    direct_admin = create_platform_admin("capable-direct", password=None)
    group_admin = User.objects.create_user("capable-group")
    with system_context(reason="test.capabilities.group-admin"):
        group = Group.objects.create(name="Capable administrators")
        group.add_member(str(to_subject_ref(group_admin)))
        grant_role(actor=group, role="angee/role:admin")
    plain = User.objects.create_user("capable-plain")

    # A live roster reaches the manager role, but not IAM's administrator constant.
    assert _capabilities(roster_manager) == ["manage_people"]
    # A direct grant and a group-held one reach both arms.
    assert _capabilities(direct_admin) == ["audit_people", "manage_people"]
    assert _capabilities(group_admin) == ["audit_people", "manage_people"]
    assert _capabilities(plain) == []


def test_a_capability_leaves_with_the_path_that_granted_it(spaces_tables):
    actor = User.objects.create_user("capable-leaving")
    with system_context(reason="test.capabilities.leaving"):
        group = Group.objects.create(name="Leaving administrators")
        member = str(to_subject_ref(actor))
        group.add_member(member)
        grant_role(actor=group, role="angee/role:admin")
    assert _capabilities(actor) == ["audit_people", "manage_people"]

    with system_context(reason="test.capabilities.leaving"):
        group.remove_member(member)
    assert _capabilities(actor) == []


def test_anonymous_sessions_hold_and_learn_no_capability(spaces_tables):
    anonymous = execute_schema(_schema("public"), IDENTITY, user=AnonymousUser())
    assert result_data(anonymous) == {"current_user": None, "declared_capabilities": []}
    assert subject_capabilities(None) == []


def test_signed_in_sessions_learn_every_declared_name(spaces_tables):
    plain = User.objects.create_user("capable-names")
    assert declared_capabilities() == ["audit_people", "manage_people"]
    assert _identity(plain)["declared_capabilities"] == ["audit_people", "manage_people"]
