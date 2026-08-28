"""Generic scope demo regressions for framework-owned REBAC machinery."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from rebac import RelationshipTuple, system_context, to_object_ref, to_subject_ref, write_relationships
from rebac.backends import backend

from tests.conftest import create_user
from tests.scopedemo.models import SCOPE_MEMBER_RELATION, Scope


@pytest.mark.django_db
def test_parent_scope_member_reaches_child_scope() -> None:
    """The generic field-backed parent relation grants descendant reach."""

    call_command("rebac", "sync", verbosity=0)
    member = create_user("scope-parent-member")
    outsider = create_user("scope-outsider")
    with system_context(reason="test scopedemo hierarchy reach"):
        parent = Scope.objects.create(name="Parent")
        child = Scope.objects.create(name="Child", parent=parent)
    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(parent),
                relation=SCOPE_MEMBER_RELATION,
                subject=to_subject_ref(member),
            )
        ]
    )

    assert backend().check_access(
        subject=to_subject_ref(member),
        action="read",
        resource=to_object_ref(child),
    )
    assert not backend().check_access(
        subject=to_subject_ref(outsider),
        action="read",
        resource=to_object_ref(child),
    )
