"""Presence refs: the ``<app_label.ModelName>#<permission>`` refs the session's identity holds at type level."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rebac import (
    ObjectRef,
    RelationshipTuple,
    backend,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.roles import grant

from angee.iam.roles import subject_permitted
from tests.conftest import addon_schema, create_platform_admin, execute_schema, result_data

User = get_user_model()
iam_schema = importlib.import_module("angee.iam.schema")
PERMITTED = "query Permitted($refs: [String!]!) { current_user { username permitted(refs: $refs) } }"
ADMIN_REFS = ["iam.User#create", "iam.User#read__last_login", "iam.User#read"]


def _permitted(user: Any, refs: list[str]) -> Any:
    """Ask the public identity read which of ``refs`` ``user`` holds."""

    return execute_schema(addon_schema(iam_schema.schemas, "public"), PERMITTED, {"refs": refs}, user=user)


def _held(user: Any, refs: list[str]) -> list[str]:
    return result_data(_permitted(user, refs))["current_user"]["permitted"]


def test_a_role_holder_holds_its_refs_and_others_do_not(composed_tables: None) -> None:
    """The administrator role decides IAM's own arms, held directly or through a group."""

    direct = create_platform_admin("permitted-admin")
    through_group = User.objects.create_user(username="permitted-group-admin")
    with system_context(reason="test.permitted.group-admin"):
        group = iam_schema.Group.objects.create(name="Permitted administrators")
        group.add_member(str(to_subject_ref(through_group)))
        grant(actor=group, role="angee/role:admin")
    plain = User.objects.create_user(username="permitted-plain")

    assert result_data(_permitted(direct, ADMIN_REFS))["current_user"] == {
        "username": "permitted-admin",
        "permitted": ADMIN_REFS,
    }
    assert _held(through_group, ADMIN_REFS) == ADMIN_REFS
    assert _held(plain, ADMIN_REFS) == []
    # Each ref is answered once, in the order first asked.
    assert _held(direct, ["iam.User#read", "iam.User#create", "iam.User#read"]) == ["iam.User#read", "iam.User#create"]


def test_anonymous_sessions_hold_no_ref(composed_tables: None) -> None:
    """Signed out there is no identity to hold a ref."""

    assert result_data(_permitted(AnonymousUser(), ADMIN_REFS)) == {"current_user": None}
    assert subject_permitted(None, ADMIN_REFS) == []


def test_an_unknown_model_or_permission_fails_loudly(composed_tables: None) -> None:
    """A mistyped ref is an error, never a silently missing entry."""

    admin = create_platform_admin("permitted-typo")
    for ref, problem in (
        ("iam.Nobody#read", 'Unknown model in "iam.Nobody#read"'),
        ("User#read", 'Unknown model in "User#read"'),
        ("iam.User#raed", 'Unknown permission in "iam.User#raed"'),
        ("iam.User", 'Unknown permission in "iam.User"'),
    ):
        result = _permitted(admin, ["iam.User#create", ref])
        assert result.errors is not None
        assert problem in result.errors[0].message
        # Validation does not depend on who asks.
        with pytest.raises(ValueError, match="Unknown"):
            subject_permitted(None, [ref])


def test_a_row_dependent_permission_is_false_at_type_level(composed_tables: None) -> None:
    """A grant on one row reads that row; only an arm reading every row holds the ref."""

    reader = User.objects.create_user(username="permitted-reader")
    person = User.objects.create_user(username="permitted-person")
    write_relationships([
        RelationshipTuple(resource=to_object_ref(person), relation="directory_reader", subject=to_subject_ref(reader)),
    ])

    assert backend().check_access(
        subject=to_subject_ref(reader), action="read", resource=to_object_ref(person),
    ).allowed
    assert _held(reader, ["iam.User#read"]) == []

    # The platform directory is const-backed: its readers read every person, so the ref holds.
    directory = ObjectRef("iam/directory", "main")
    write_relationships([RelationshipTuple(resource=directory, relation="reader", subject=to_subject_ref(reader))])
    assert _held(reader, ["iam.User#read"]) == ["iam.User#read"]
