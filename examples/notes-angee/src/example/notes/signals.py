"""Ownership relationships for notes, derived from the acting user.

Creating a note grants the creator the ``owner`` relation on ``notes/note``;
deleting a note clears its relationships. Owner grants are what make a note
readable and writable by its creator under the permission schema, so they are
wired to the model lifecycle rather than left to callers. The creator/updater
audit ids are stamped by the framework ``AuditMixin`` handler, not here.
"""

from __future__ import annotations

from typing import Any

from django.db.models import Model
from django.db.models.signals import post_delete, post_save
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    delete_relationships,
    write_relationships,
)
from rebac.types import RelationshipFilter

RESOURCE_TYPE = "notes/note"
USER_TYPE = "auth/user"


def connect() -> None:
    """Wire note ownership to the note lifecycle (idempotent)."""

    post_save.connect(grant_owner, dispatch_uid="notes.grant_owner")
    post_delete.connect(clear_relationships, dispatch_uid="notes.clear")


def grant_owner(
    sender: type[Model],
    instance: Model,
    created: bool = False,
    raw: bool = False,
    **_: Any,
) -> None:
    """Grant the creator the ``owner`` relation on a new note."""

    if raw or not created or not _is_note(sender):
        return
    owner_id = getattr(instance, "created_by_id", None)
    if owner_id is None:
        return
    write_relationships(
        [
            RelationshipTuple(
                resource=ObjectRef(RESOURCE_TYPE, str(instance.sqid)),
                relation="owner",
                subject=SubjectRef.of(USER_TYPE, str(owner_id)),
            )
        ]
    )


def clear_relationships(
    sender: type[Model], instance: Model, **_: Any
) -> None:
    """Drop every relationship pointing at a deleted note."""

    if not _is_note(sender):
        return
    delete_relationships(
        RelationshipFilter(
            resource_type=RESOURCE_TYPE,
            resource_id=str(instance.sqid),
        )
    )


def _is_note(sender: type[Model]) -> bool:
    """Return true for the composed ``notes.Note`` model."""

    return sender._meta.label_lower == "notes.note"
