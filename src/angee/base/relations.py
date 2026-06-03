"""Shared REBAC relationship-write primitives for Angee addons.

Kept free of ``angee.base.graphql`` imports so model modules can import it
during app population without a circular import.
"""

from __future__ import annotations

from typing import Any

from rebac import (
    RelationshipTuple,
    delete_relationships,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)
from rebac.types import RelationshipFilter


def grant_owner(resource: Any, subject: Any) -> None:
    """Write one ``owner`` relationship tuple for a newly created resource.

    Runs inside the caller's ambient ``system_context`` + ``transaction.atomic``
    so the owner grant commits or rolls back together with the row it owns.
    """

    write_relationships(
        [
            RelationshipTuple(
                resource=to_object_ref(resource),
                relation="owner",
                subject=to_subject_ref(subject),
            )
        ]
    )


def revoke_owner(resource: Any, subject: Any) -> None:
    """Delete this subject's ``owner`` relationship tuple for one resource."""

    resource_ref = to_object_ref(resource)
    subject_ref = to_subject_ref(subject)
    delete_relationships(
        RelationshipFilter(
            resource_type=resource_ref.resource_type,
            resource_id=resource_ref.resource_id,
            relation="owner",
            subject_type=subject_ref.subject_type,
            subject_id=subject_ref.subject_id,
            optional_subject_relation=subject_ref.optional_relation,
        )
    )
