"""Tests for IAM permission-hub GraphQL surfaces."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, cast

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory
from rebac import ObjectRef, actor_context, system_context
from rebac.actors import to_subject_ref
from rebac.models import active_relationship_model
from rebac.roles import ROLE_RELATION, grant
from strawberry import relay

from angee.base.apps import SCHEMA_PART_KEYS
from angee.base.graphql.schema import GraphQLSchemas
from angee.iam.signals import PLATFORM_ADMIN_ROLE
from tests.conftest import _create_missing_tables as _create_connection_tables

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")


def test_permission_hub_queries_are_admin_only(
    iam_permission_hub_tables: None,
) -> None:
    """Console permission-hub queries require platform-admin reach."""

    plain = User.objects.create_user(username="hub-plain", email="plain@example.com")
    admin = _platform_admin("hub-admin")
    target = User.objects.create_user(username="hub-target", email="target@example.com")
    grant(actor=target, role="angee/role:auditor")
    console_schema = _schema("console")
    queries = [
        """
        query {
          users(pagination: {limit: 10}) {
            totalCount
            results { username }
          }
        }
        """,
        """
        query {
          roles { id namespace label }
        }
        """,
        """
        query {
          grants(pagination: {limit: 10}) {
            totalCount
            results { principalId principalType role }
          }
        }
        """,
        """
        query {
          rebacSchema {
            resourceType
            relations { name allowedSubjectTypes }
            permissions { name conditions { name } }
          }
        }
        """,
        """
        query {
          relationships(pagination: {limit: 10}) {
            totalCount
            results {
              resourceType
              resourceId
              relation
              subjectType
              subjectId
              subjectRelation
              caveatName
            }
          }
        }
        """,
    ]

    for query in queries:
        denied = _execute(console_schema, query, user=plain)
        assert denied.errors is not None

        allowed = _execute(console_schema, query, user=admin)
        assert allowed.errors is None


def test_roles_query_excludes_role_types_missing_from_rebac_schema(
    iam_permission_hub_tables: None,
) -> None:
    """Tuple-derived roles are limited to resource types in the installed schema."""

    admin = _platform_admin("hub-role-filter-admin")
    target = User.objects.create_user(
        username="hub-role-filter-target",
        email="target@example.com",
    )
    grant(actor=target, role="angee/role:admin")
    subject_ref = to_subject_ref(target)
    with system_context(reason="test orphaned role tuple"):
        active_relationship_model().objects.create(
            resource_type="notes/role",
            resource_id="note_admin",
            relation=ROLE_RELATION,
            subject_type=subject_ref.subject_type,
            subject_id=subject_ref.subject_id,
            optional_subject_relation=subject_ref.optional_relation,
            caveat_name="",
            caveat_context=None,
            expires_at=None,
        )
    console_schema = _schema("console")

    data = _data(
        _execute(
            console_schema,
            """
            query {
              roles { id namespace label }
              grants(pagination: {limit: 10}) {
                results { role }
              }
            }
            """,
            user=admin,
        )
    )
    roles = data["roles"]
    grants = data["grants"]["results"]
    schema_role_types = iam_schema._schema_role_resource_types()

    assert {"id": "admin", "namespace": "angee", "label": "Admin"} in roles
    assert {"id": "note_admin", "namespace": "notes", "label": "Note Admin"} not in roles
    assert {f"{role['namespace']}/role" for role in roles} <= schema_role_types
    assert "angee/role:admin" in {grant["role"] for grant in grants}
    assert "notes/role:note_admin" not in {grant["role"] for grant in grants}
    assert {grant["role"].split(":", 1)[0] for grant in grants} <= schema_role_types


def test_grants_query_labels_principals_by_display_name(
    iam_permission_hub_tables: None,
) -> None:
    """The grants list surfaces each principal's display name, not a raw id."""

    admin = _platform_admin("hub-label-admin")
    named = User.objects.create_user(
        username="hub-label-named",
        email="named@example.com",
        first_name="Named",
        last_name="Owner",
    )
    plain = User.objects.create_user(
        username="hub-label-plain",
        email="plain@example.com",
    )
    grant(actor=named, role="angee/role:admin")
    grant(actor=plain, role="angee/role:admin")

    data = _data(
        _execute(
            _schema("console"),
            """
            query {
              grants(pagination: {limit: 50}) {
                results { principalId principalLabel }
              }
            }
            """,
            user=admin,
        )
    )
    labels = {
        row["principalId"]: row["principalLabel"]
        for row in data["grants"]["results"]
    }
    assert labels[str(named.pk)] == "Named Owner"
    assert labels[str(plain.pk)] == "hub-label-plain"


def test_permission_hub_mutations_are_admin_only(
    iam_permission_hub_tables: None,
) -> None:
    """Console role-grant mutations require platform-admin reach."""

    plain = User.objects.create_user(username="hub-mutate-plain", email="plain@example.com")
    admin = _platform_admin("hub-mutate-admin")
    target = User.objects.create_user(username="hub-mutate-target", email="target@example.com")
    console_schema = _schema("console")
    grant_mutation = """
        mutation Grant($principalId: String!, $role: String!) {
          grantRole(principalId: $principalId, role: $role)
        }
    """
    revoke_mutation = """
        mutation Revoke($principalId: String!, $role: String!) {
          revokeRole(principalId: $principalId, role: $role)
        }
    """
    variables = {
        "principalId": str(target.pk),
        "role": "angee/role:console_operator",
    }

    denied_grant = _execute(console_schema, grant_mutation, variables, user=plain)
    assert denied_grant.errors is not None

    granted = _data(_execute(console_schema, grant_mutation, variables, user=admin))
    assert granted["grantRole"] is True

    denied_revoke = _execute(console_schema, revoke_mutation, variables, user=plain)
    assert denied_revoke.errors is not None

    revoked = _data(_execute(console_schema, revoke_mutation, variables, user=admin))
    assert revoked["revokeRole"] is True


def test_grant_role_then_revoke_role_writes_and_removes_role_tuple(
    iam_permission_hub_tables: None,
) -> None:
    """The permission-hub mutations persist and remove direct role tuples."""

    admin = _platform_admin("hub-write-admin")
    target = User.objects.create_user(username="hub-write-target", email="target@example.com")
    console_schema = _schema("console")
    variables = {
        "principalId": str(target.pk),
        "role": "angee/role:tuple_writer",
    }

    granted = _data(
        _execute(
            console_schema,
            """
            mutation Grant($principalId: String!, $role: String!) {
              grantRole(principalId: $principalId, role: $role)
            }
            """,
            variables,
            user=admin,
        )
    )

    assert granted["grantRole"] is True
    assert _role_membership_exists(target, variables["role"])

    revoked = _data(
        _execute(
            console_schema,
            """
            mutation Revoke($principalId: String!, $role: String!) {
              revokeRole(principalId: $principalId, role: $role)
            }
            """,
            variables,
            user=admin,
        )
    )

    assert revoked["revokeRole"] is True
    assert not _role_membership_exists(target, variables["role"])


def test_grant_role_accepts_user_relay_global_id(
    iam_permission_hub_tables: None,
) -> None:
    """The grant mutation accepts the relay global id exposed by UserType.id."""

    admin = _platform_admin("hub-relay-admin")
    target = User.objects.create_user(
        username="hub-relay-target",
        email="target@example.com",
    )
    console_schema = _schema("console")
    node_id = str(getattr(target, "sqid", target.pk))
    variables = {
        "principalId": relay.to_base64(iam_schema.UserType, node_id),
        "role": "angee/role:relay_writer",
    }

    granted = _data(
        _execute(
            console_schema,
            """
            mutation Grant($principalId: String!, $role: String!) {
              grantRole(principalId: $principalId, role: $role)
            }
            """,
            variables,
            user=admin,
        )
    )

    assert granted["grantRole"] is True
    assert _role_membership_exists(target, variables["role"])

    revoked = _data(
        _execute(
            console_schema,
            """
            mutation Revoke($principalId: String!, $role: String!) {
              revokeRole(principalId: $principalId, role: $role)
            }
            """,
            variables,
            user=admin,
        )
    )

    assert revoked["revokeRole"] is True
    assert not _role_membership_exists(target, variables["role"])


def test_grant_role_ignores_non_user_relay_global_id(
    iam_permission_hub_tables: None,
) -> None:
    """A non-user relay global id is handled as a raw principal id."""

    admin = _platform_admin("hub-relay-guard-admin")
    target = User.objects.create_user(
        username="hub-relay-guard-target",
        email="target@example.com",
    )
    console_schema = _schema("console")
    role = "angee/role:relay_guard"
    result = _execute(
        console_schema,
        """
        mutation Grant($principalId: String!, $role: String!) {
          grantRole(principalId: $principalId, role: $role)
        }
        """,
        {
            "principalId": relay.to_base64(
                iam_schema.VendorType,
                str(getattr(target, "sqid", target.pk)),
            ),
            "role": role,
        },
        user=admin,
    )

    assert result.errors is not None
    assert not _role_membership_exists(target, role)


def test_revoke_role_returns_false_for_missing_membership(
    iam_permission_hub_tables: None,
) -> None:
    """No-op role revocation reports false."""

    admin = _platform_admin("hub-noop-admin")
    target = User.objects.create_user(username="hub-noop-target", email="target@example.com")
    role = "angee/role:missing"
    console_schema = _schema("console")

    result = _data(
        _execute(
            console_schema,
            """
            mutation Revoke($principalId: String!, $role: String!) {
              revokeRole(principalId: $principalId, role: $role)
            }
            """,
            {"principalId": str(target.pk), "role": role},
            user=admin,
        )
    )

    assert result["revokeRole"] is False
    assert not _role_membership_exists(target, role)


def test_role_refs_are_current_user_only(
    iam_permission_hub_tables: None,
) -> None:
    """Role refs are exposed only for the session user."""

    alice = User.objects.create_user(username="hub-alice", email="alice@example.com")
    bob = User.objects.create_user(username="hub-bob", email="bob@example.com")
    grant(actor=alice, role="angee/role:alice_only")
    grant(actor=bob, role="angee/role:bob_only")
    public_schema = _schema("public")

    data = _data(
        _execute(
            public_schema,
            """
            query {
              currentUser { username roleRefs }
            }
            """,
            user=alice,
        )
    )

    assert data["currentUser"] == {
        "username": "hub-alice",
        "roleRefs": ["angee/role:alice_only"],
    }
    public_sdl = public_schema.as_str()
    assert "users(" not in public_sdl
    assert "grants(" not in public_sdl
    assert "relationships(" not in public_sdl
    assert "roleRefs" not in _type_block(public_sdl, "UserType")


@pytest.fixture()
def iam_permission_hub_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete source-addon tables and sync REBAC schema."""

    del transactional_db
    created_models = _create_connection_tables()
    call_command("rebac", "sync", verbosity=0)
    try:
        yield
    finally:
        if created_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_models):
                    schema_editor.delete_model(model)


def _platform_admin(username: str) -> Any:
    """Create a superuser with the platform-admin role tuple present."""

    admin = User.objects.create_superuser(
        username=username,
        email=f"{username}@example.com",
        password="admin",
    )
    grant(actor=admin, role=PLATFORM_ADMIN_ROLE)
    return admin


def _role_membership_exists(user: Any, role: str) -> bool:
    """Return whether ``user`` directly holds ``role``."""

    subject_ref = to_subject_ref(user)
    role_ref = ObjectRef.parse(role)
    with system_context(reason="test iam permission hub assertions"):
        return active_relationship_model().objects.filter(
            resource_type=role_ref.resource_type,
            resource_id=role_ref.resource_id,
            relation=ROLE_RELATION,
            subject_type=subject_ref.subject_type,
            subject_id=subject_ref.subject_id,
            optional_subject_relation=subject_ref.optional_relation,
            caveat_name="",
        ).exists()


def _schema(name: str) -> Any:
    """Build one IAM-only GraphQL schema bucket."""

    entry = iam_schema.schemas[name]
    parts = {
        key: tuple(entry.get(key, ()))
        for key in SCHEMA_PART_KEYS
    }
    return GraphQLSchemas.from_addons([_Addon({name: parts})]).build(name)


def _execute(
    schema: Any,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    user: Any | None = None,
    request: Any | None = None,
) -> Any:
    """Execute a GraphQL operation with a request-shaped context."""

    request = request or _request(user or AnonymousUser())
    actor = getattr(request, "user", AnonymousUser())
    with actor_context(actor):
        return schema.execute_sync(
            query,
            variable_values=variables or {},
            context_value=SimpleNamespace(request=request),
        )


def _data(result: Any) -> dict[str, Any]:
    """Return result data after asserting the operation succeeded."""

    assert result.errors is None
    assert result.data is not None
    return cast(dict[str, Any], result.data)


def _request(user: Any) -> Any:
    """Return a request object with a minimal mutable session."""

    request = RequestFactory().post("/graphql/console/")
    request.user = user
    request.session = _Session()
    return request


def _type_block(sdl: str, type_name: str) -> str:
    """Return one GraphQL type block from ``sdl``."""

    marker = f"type {type_name} "
    start = sdl.index(marker)
    end = sdl.index("\n}", start) + 2
    return sdl[start:end]


class _Session(dict[str, Any]):
    """Minimal session object for direct GraphQL execution."""

    modified = False

    def cycle_key(self) -> None:
        """Mark the fake session as cycled."""

        self.modified = True

    def flush(self) -> None:
        """Clear the fake session."""

        self.clear()
        self.modified = True


class _Addon:
    """Small addon stand-in exposing normalized schema parts."""

    def __init__(self, schema_parts: dict[str, dict[str, tuple[object, ...]]]) -> None:
        self.schema_parts = schema_parts
