"""Role grants retain recursive reach through relation-backed arrows."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from rebac import ObjectRef, RelationshipTuple, SubjectRef, system_context, to_subject_ref
from rebac.backends import backend
from rebac.relationships import delete_relationships, write_relationships
from rebac.types import RelationshipFilter


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("storage", ("denormalized", "registry"))
@pytest.mark.parametrize("kind", ("person", "service"))
def test_role_arrows_preserve_transitive_membership_and_revocation(settings, storage: str, kind: str) -> None:
    """Plain role subjects grant inherited reach without granting direct membership."""

    settings.REBAC_LOCAL_BACKEND_STORAGE = storage
    call_command("rebac", "sync", verbosity=0)
    with system_context(reason="test role inheritance"):
        user = get_user_model().objects.create_user(username=f"role-{storage}-{kind}", kind=kind)
        subject = to_subject_ref(user)
        child = ObjectRef("storage/role", "child")
        parent = ObjectRef("storage/role", "parent")
        root = ObjectRef("storage/role", "root")
        write_relationships(
            [
                RelationshipTuple(child, "member", subject),
                RelationshipTuple(parent, "includes", SubjectRef(child)),
                RelationshipTuple(root, "includes", SubjectRef(parent)),
            ]
        )

    for role in (child, parent, root):
        assert backend().check_access(subject=subject, action="effective_member", resource=role).allowed
    assert not backend().check_access(subject=subject, action="member", resource=parent).allowed
    assert not backend().check_access(
        subject=subject,
        action="effective_member",
        resource=ObjectRef("storage/role", "unrelated"),
    ).allowed
    assert {"child", "parent", "root"} <= set(
        backend().accessible(subject=subject, action="effective_member", resource_type="storage/role")
    )

    with system_context(reason="test role inheritance revocation"):
        delete_relationships(
            RelationshipFilter(
                resource_type=parent.resource_type,
                resource_id=parent.resource_id,
                relation="includes",
                subject_type=child.resource_type,
                subject_id=child.resource_id,
                optional_subject_relation="",
            )
        )

    assert backend().check_access(subject=subject, action="effective_member", resource=child).allowed
    for role in (parent, root):
        assert not backend().check_access(subject=subject, action="effective_member", resource=role).allowed
    assert set(
        backend().accessible(subject=subject, action="effective_member", resource_type="storage/role")
    ) == {"child"}
