"""IAM's GraphQL creation, credential, identity and extension-role contracts."""

from types import SimpleNamespace

import pytest
import strawberry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model

from tests.conftest import addon_schema, create_platform_admin, execute_schema, graphql_request, result_data
from tests.iam_campaign import Person
from tests.iam_campaign import iam_admin as iam_admin
from tests.iam_campaign import legacy_person_emails as legacy_person_emails
from tests.spaces_models import Group, Membership
from tests.test_iam_graphql import _schema
from tests.test_spaces import spaces_tables as spaces_tables

User = get_user_model()
CREATE = """mutation($object: users_insert_input!) {
  insert_users_one(object: $object) { id username email is_staff is_active }
}"""
ISSUE = "mutation($id: ID!) { issue_user_password(id: $id) { password } }"


@pytest.mark.parametrize("password", [None, "graphql-initial-secret"])
def test_graphql_insert_uses_person_factory_and_password_extension(iam_admin, password):
    values = {"username": "graphql-person", "email": " GRAPHQL@EXAMPLE.COM "}
    if password is not None:
        values["password"] = password
    result = result_data(execute_schema(_schema("console"), CREATE, {"object": values}, user=iam_admin))
    row = result["insert_users_one"]
    assert row["email"] == "graphql@example.com"
    assert row["is_staff"] is False and row["is_active"] is True
    user = User._base_manager.get(username="graphql-person")
    assert user.has_usable_password() is (password is not None)
    if password:
        assert user.check_password(password)
        assert password not in str(result)
    assert Person._base_manager.filter(user=user).count() == 1


@pytest.mark.parametrize(
    "field,value", [("is_staff", True), ("is_active", False), ("is_superuser", True), ("kind", "service")]
)
def test_graphql_insert_rejects_authority_fields(iam_admin, field, value):
    result = execute_schema(
        _schema("console"),
        CREATE,
        {"object": {"username": "override", field: value}},
        user=iam_admin,
    )
    assert result.errors
    assert f"Field '{field}' is not defined by type 'users_insert_input'" in result.errors[0].message
    assert not User._base_manager.filter(username="override").exists()


def test_graphql_duplicate_creation_returns_only_account_exists_code(iam_admin):
    user = User.objects.create_user("private-account-name", "claimed@example.com", is_active=False)
    result = execute_schema(
        _schema("console"),
        CREATE,
        {"object": {"username": "duplicate", "email": " CLAIMED@EXAMPLE.COM "}},
        user=iam_admin,
    )
    assert result.errors and len(result.errors) == 1
    error = result.errors[0]
    assert error.message == "ACCOUNT_EXISTS"
    assert error.extensions == {"code": "ACCOUNT_EXISTS"}
    assert user.username not in str(error.formatted) and user.email not in str(error.formatted)
    assert not User._base_manager.filter(username="duplicate").exists()


def test_ambiguous_email_refusal_crosses_graphql_with_only_its_wire_code(legacy_person_emails):
    for username in ("private-first", "private-second"):
        User.objects.create_user(username, "ambiguous@example.com")

    @strawberry.type
    class LookupProbe:
        """Test-only boundary probe: IAM deliberately exports no email-lookup field."""

        @strawberry.field
        def resolve_person(self) -> bool:
            return User.objects.person_for_email("ambiguous@example.com") is not None

    schema = addon_schema({"public": {"query": (LookupProbe,)}}, "public")
    result = execute_schema(schema, "{ resolve_person }")
    assert result.errors and len(result.errors) == 1
    assert result.errors[0].message == "ACCOUNT_EMAIL_AMBIGUOUS"
    assert result.errors[0].extensions == {"code": "ACCOUNT_EMAIL_AMBIGUOUS"}
    assert "ambiguous@example.com" not in str(result.errors[0].formatted)


def test_graphql_password_issue_is_gated_and_returns_credential_only_once(iam_admin):
    target = User.objects.create_user("passwordless")
    plain = User.objects.create_user("plain")
    schema = _schema("console")
    assert "password" not in schema._schema.get_type("UserType").fields
    variables = {"id": target.public_id}
    assert execute_schema(schema, ISSUE, variables, user=plain).errors
    assert not User._base_manager.get(pk=target.pk).has_usable_password()
    password = result_data(execute_schema(schema, ISSUE, variables, user=iam_admin))["issue_user_password"]["password"]
    stored = User._base_manager.get(pk=target.pk)
    assert stored.password != password and stored.check_password(password)
    replay = execute_schema(schema, ISSUE, variables, user=iam_admin)
    assert replay.errors
    assert password not in str(replay.data) and password not in str([e.formatted for e in replay.errors])
    assert User._base_manager.get(pk=target.pk).password == stored.password


def test_graphql_create_denies_plain_and_anonymous_actors_without_inserting(iam_admin):
    schema = _schema("console")
    plain = User.objects.create_user("plain")
    for actor in (plain, AnonymousUser()):
        result = execute_schema(schema, CREATE, {"object": {"username": "denied"}}, user=actor)
        assert result.errors
        assert not User._base_manager.filter(username="denied").exists()


@pytest.mark.parametrize(
    "attrs", [{"is_active": False}, {"is_staff": True}, {"is_superuser": True}, {"kind": "service"}]
)
def test_graphql_issue_refuses_ineligible_targets_without_returning_a_secret(iam_admin, attrs):
    target = User.objects.create_user("ineligible", **attrs)
    result = execute_schema(_schema("console"), ISSUE, {"id": target.public_id}, user=iam_admin)
    assert result.errors
    assert result.data is None
    assert not User._base_manager.get(pk=target.pk).has_usable_password()


def test_graphql_current_and_real_user_and_preview_list_follow_request_identity(iam_admin):
    target = User.objects.create_user("viewed")
    peer = User.objects.create_user("peer")
    User.objects.create_user("staff", is_staff=True)
    User.objects.create_user("superuser", is_superuser=True)
    create_platform_admin("other-admin", password=None)
    schema = _schema("console")
    query = "{ current_user { username } real_user { username } viewable_people { username } }"
    ordinary = result_data(execute_schema(schema, query, user=iam_admin))
    assert ordinary["current_user"] == {"username": iam_admin.username}
    assert ordinary["real_user"] is None
    assert {r["username"] for r in ordinary["viewable_people"]} == {target.username, peer.username}
    request = graphql_request(target)
    request.view_as = SimpleNamespace(real_user=iam_admin, target=target)
    viewed = result_data(execute_schema(schema, query, request=request))
    assert viewed["current_user"] == {"username": target.username}
    assert viewed["real_user"] == {"username": iam_admin.username}
    assert viewed["viewable_people"] == ordinary["viewable_people"]
    assert result_data(execute_schema(schema, query, user=target))["viewable_people"] == []


@pytest.mark.parametrize("role,permitted", [("owner", True), ("moderator", True), ("member", False), ("viewer", False)])
def test_const_manager_role_tracks_roster_create_and_issue_but_never_preview(spaces_tables, role, permitted):
    actor = User.objects.create_user("roster-actor")
    target = User.objects.create_user("target")
    with system_context(reason="test.role.roster"):
        group = Group.objects.create(name="Account managers", slug="account-managers")
        person = Person.objects.for_user(actor)
        membership = Membership.objects.create(group=group, party=person, role=role)
        membership.confirm()
    write_relationships(
        [
            RelationshipTuple(
                resource=ObjectRef("extcontrib/role", "manager"),
                relation="team",
                subject=SubjectRef.of("spaces/group", str(group.pk)),
            ),
        ]
    )
    with actor_context(actor):
        if permitted:
            assert User.objects.check_create() == to_subject_ref(actor)
        else:
            with pytest.raises(PermissionDenied):
                User.objects.check_create()
        assert target.with_actor(actor).has_access("issue_password") is permitted
        assert not target.has_access("view_as")
    assert User.objects.viewable_people(actor) == []
    schema = _schema("console")
    assert result_data(execute_schema(schema, "{ viewable_people { username } }", user=actor))["viewable_people"] == []
    created = execute_schema(schema, CREATE, {"object": {"username": "by-roster"}}, user=actor)
    issued = execute_schema(schema, ISSUE, {"id": target.public_id}, user=actor)
    assert bool(created.errors) is not permitted
    assert bool(issued.errors) is not permitted
    if permitted:
        assert Person._base_manager.filter(user__username="by-roster").count() == 1
        password = result_data(issued)["issue_user_password"]["password"]
        assert User._base_manager.get(pk=target.pk).check_password(password)
    assert not active_relationship_model().objects.filter(resource_type="extcontrib/role", relation="member").exists()
    with system_context(reason="test.role.roster-demotion"):
        membership.role = Membership.MembershipRole.MEMBER
        membership.save(update_fields=["role"])
    with actor_context(actor), pytest.raises(PermissionDenied):
        User.objects.check_create()
    assert not target.with_actor(actor).has_access("issue_password")
