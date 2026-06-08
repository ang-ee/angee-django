"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

from datetime import datetime
from typing import cast

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto, relay
from strawberry_django.pagination import OffsetPaginated

from angee.graphql.aggregates import rebac_aggregate_builder
from angee.graphql.crud import crud
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

    id: relay.GlobalID
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


# Aggregation is owned by ``strawberry-django-aggregates``: it emits the
# group-by surface (offset-paginated groups, multi-axis composite keys, the
# full granularity track, having, and ordering). Angee contributes the
# REBAC-scoped queryset hook. Group-by axes are non-gated read fields only:
# ``is_starred`` and ``reminder_at`` are owner-gated reads (``read__*``), so
# exposing either as an axis would leak owner-only values through bucket keys.
# Count is the M2 measure; ``word_count`` is the summable numeric column.
# ``enable_filter_echo`` adds a ``filter: JSON!`` to each grouped bucket: a value
# shaped like ``notes(filters:)`` that re-selects that bucket's rows, so a client
# can lazily page a group's items through the existing scoped list query. The
# status axis is a choices column exposed as a GraphQL enum, so the echo must
# emit the enum wire name (``DRAFT``) not the stored value (``draft``) —
# resolved from the live filter type by the library (>=0.4.1).
_note_aggregates = rebac_aggregate_builder(
    model=Note,
    aggregate_fields=["id", "word_count"],
    group_by_fields=["status", "updated_at"],
    filter_type=NoteFilter,
    pagination_style="offset",
    enable_filter_echo=True,
).build()


@strawberry.type
class NotesQuery:
    """Public notes queries."""

    notes: OffsetPaginated[NoteType] = strawberry_django.offset_paginated(
        filters=NoteFilter,
        order=NoteOrder,
    )
    note: NoteType | None = strawberry_django.node()
    note_aggregate = _note_aggregates.aggregate_field
    note_groups = _note_aggregates.group_by_field


_AGGREGATE_TYPES = [
    _note_aggregates.aggregate_type,
    _note_aggregates.grouped_type,
    _note_aggregates.grouped_result_type,
    _note_aggregates.group_key_type,
]


_NOTE_SCHEMA_BUCKET = {
    "query": [NotesQuery, revisions(NoteType)],
    "mutation": [crud(NoteType, create=NoteInput, update=NotePatch, delete=True)],
    "types": [NoteType, *_AGGREGATE_TYPES],
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
