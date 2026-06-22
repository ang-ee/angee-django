"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

from datetime import datetime
from typing import cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.crud import crud
from angee.graphql.data import data_query
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_display_label, user_public_id

Note = apps.get_model("notes", "Note")


@strawberry_django.type(Note)
class NoteType(AngeeNode):
    """GraphQL projection of a note."""

    title: auto
    body: auto
    status: auto
    tags: auto
    is_starred: auto
    reminder_at: auto
    created_at: auto
    updated_at: auto
    word_count: auto

    @strawberry_django.field(only=["created_by_id"])
    def created_by(self) -> strawberry.ID | None:
        """Return the creator's public id without exposing the user object."""

        return cast("strawberry.ID | None", user_public_id(self.created_by_id))

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self) -> str | None:
        """Return the creator's display label - no user object exposed."""

        return user_display_label(self.created_by_id)

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the updater's public id without exposing the user object."""

        return cast("strawberry.ID | None", user_public_id(self.updated_by_id))

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by_label(self) -> str | None:
        """Return the updater's display label - no user object exposed."""

        return user_display_label(self.updated_by_id)


@strawberry.input
class NoteInput:
    """Fields accepted when creating a note."""

    title: str
    body: str = ""
    status: Note.Status = Note.Status.DRAFT
    tags: list[str] = strawberry.field(default_factory=list)
    is_starred: bool = False
    reminder_at: datetime | None = None


@strawberry.input
class NotePatch:
    """Fields accepted when updating a note."""

    id: PublicID
    title: str | None = strawberry.UNSET
    body: str | None = strawberry.UNSET
    status: Note.Status | None = strawberry.UNSET
    tags: list[str] | None = strawberry.UNSET
    is_starred: bool | None = strawberry.UNSET
    reminder_at: datetime | None = strawberry.UNSET


@strawberry_django.filter_type(Note, lookups=True)
class NoteFilter:
    """Field lookups accepted when filtering the notes connection.

    Every grouped-aggregate axis (see ``group_by_fields`` below) needs a
    matching field here so a bucket's ``filter`` echo can mirror it back as a
    list-query filter; ``updated_at`` is therefore filterable as well as a
    group axis.
    """

    status: auto
    is_starred: auto
    title: auto
    updated_at: auto


@strawberry_django.order_type(Note)
class NoteOrder:
    """Orderings accepted by the notes connection."""

    title: auto
    status: auto
    updated_at: auto
    created_at: auto
    word_count: auto


NotesQuery, _NOTE_DATA_TYPES = data_query(
    NoteType,
    type_name="NotesQuery",
    filters=NoteFilter,
    order=NoteOrder,
    list_name="notes",
    detail_name="note",
    aggregate_name="note_aggregate",
    group_name="note_groups",
    aggregate_fields=["id", "word_count"],
    group_by_fields=["status", "updated_at"],
    enable_filter_echo=True,
    aggregate_kwargs={"pagination_style": "offset"},
)


_NOTE_SCHEMA_BUCKET = {
    "query": [NotesQuery, revisions(NoteType)],
    "mutation": [crud(NoteType, create=NoteInput, update=NotePatch, delete=True)],
    "types": [NoteType, *_NOTE_DATA_TYPES],
}


schemas = {
    "public": {
        **_NOTE_SCHEMA_BUCKET,
    },
    "console": {
        **_NOTE_SCHEMA_BUCKET,
        "subscription": [changes(Note, field="noteChanged")],
    },
}
