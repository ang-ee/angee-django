"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from rebac import system_context
from rebac.errors import MissingActorError
from strawberry import auto, relay
from strawberry_django.pagination import OffsetPaginated
from strawberry_django_aggregates import AggregateBuilder

from angee.base.models import public_id_of
from angee.graphql.aggregates import rebac_aggregate_get_queryset
from angee.graphql.crud import crud
from angee.graphql.node import AngeeNode
from angee.graphql.subscriptions import changes

Note = apps.get_model("notes", "Note")
User = apps.get_model("iam", "User")


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

        return _user_public_id(self.created_by_id)

    @strawberry_django.field(only=["created_by_id"])
    def created_by_label(self) -> str | None:
        """Return the creator's display label - no user object exposed."""

        return _user_label(self.created_by_id)

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the updater's public id without exposing the user object."""

        return _user_public_id(self.updated_by_id)

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by_label(self) -> str | None:
        """Return the updater's display label - no user object exposed."""

        return _user_label(self.updated_by_id)


@strawberry.type
class NoteRevision:
    """Versioned body snapshot for one note revision."""

    id: strawberry.ID
    created_at: datetime
    comment: str | None
    body: str

    @classmethod
    def from_version(cls, version: Any) -> NoteRevision:
        """Return GraphQL output for one django-reversion version."""

        return cls(
            id=strawberry.ID(str(version.pk)),
            created_at=cast(datetime, version.revision.date_created),
            comment=cast(str | None, version.revision.comment or None),
            body=str(version.field_dict.get("body", "")),
        )


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
_note_aggregates = AggregateBuilder(
    model=Note,
    aggregate_fields=["id", "word_count"],
    group_by_fields=["status", "updated_at"],
    filter_type=NoteFilter,
    pagination_style="offset",
    get_queryset=rebac_aggregate_get_queryset(Note.objects.all()),
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

    @strawberry.field
    def note_revisions(self, id: relay.GlobalID) -> list[NoteRevision]:
        """Return actor-visible body revisions for one note."""

        note = _scoped_note_by_id(id)
        if note is None:
            return []
        return [NoteRevision.from_version(version) for version in note.revisions]


_AGGREGATE_TYPES = [
    _note_aggregates.aggregate_type,
    _note_aggregates.grouped_type,
    _note_aggregates.grouped_result_type,
    _note_aggregates.group_key_type,
]


def _scoped_note_by_id(id: relay.GlobalID) -> Any | None:
    """Return the actor-visible note addressed by relay id, if any."""

    try:
        return Note.objects.filter(**Note.public_id_lookup(id.node_id)).first()
    except MissingActorError:
        return None


def _user_public_id(user_id: Any) -> strawberry.ID | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return strawberry.ID(public_id_of(User(id=user_id)))


def _user_label(user_id: Any) -> str | None:
    """Return a user's display label (name) without exposing the user object.

    Resolved under ``system_context`` (the elevation the User-owning IAM addon
    uses for server-side reads) so an actor-scoped note query never pulls a
    guarded User row into its own queryset (REBAC rejects that); only a display
    string leaves the resolver, never the user object. Intended for the
    single-record form — not selected as a list column.
    """

    if user_id is None:
        return None
    with system_context(reason="notes.graphql.user_label"):
        user = User.objects.filter(pk=user_id).only("first_name", "last_name", "username").first()
    if user is None:
        return None
    return str(user.get_full_name() or user.username)


_NOTE_SCHEMA_BUCKET = {
    "query": [NotesQuery],
    "mutation": [crud(NoteType, create=NoteInput, update=NotePatch, delete=True)],
    "types": [NoteType, NoteRevision, *_AGGREGATE_TYPES],
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
