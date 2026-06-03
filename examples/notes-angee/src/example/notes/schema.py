"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import QuerySet
from rebac.errors import MissingActorError
from strawberry import auto, relay
from strawberry_django_aggregates import AggregateBuilder

from angee.base.graphql import AngeeNode, OffsetPaginated, changes, crud
from angee.base.models import public_id_of

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

    @strawberry_django.field(only=["updated_by_id"])
    def updated_by(self) -> strawberry.ID | None:
        """Return the updater's public id without exposing the user object."""

        return _user_public_id(self.updated_by_id)


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


def _rebac_scoped(info: strawberry.Info | None = None) -> QuerySet[Any]:
    """Return notes with the ambient REBAC actor eagerly applied.

    The aggregates library owns aggregation but expects a pre-scoped
    queryset (its one host-agnostic seam); this hook is the only Angee
    glue. Scope must be applied eagerly: ``compute_aggregation`` runs
    ``.values().annotate()`` paths that bypass ``RebacQuerySet._fetch_all``,
    where row scoping would otherwise fire. Let the queryset owner resolve the
    ambient actor/sudo state so aggregate queries match the list query's
    permission semantics. Under REBAC strict mode a request with no actor
    raises ``MissingActorError``, so that case yields an empty result rather
    than a leak.

    ``on_field_deny("allow")`` relaxes field-read enforcement here because the
    same ``.values().annotate()`` paths do not apply per-field redaction. That
    is safe ONLY while every exposed group-by axis is a non-gated read field:
    a field with a ``read__<field>`` gate (``is_starred``, ``reminder_at``)
    must never be a ``group_by_fields`` axis, or its owner-only value leaks via
    the bucket keys/counts.
    """

    queryset = Note.objects.all().on_field_deny("allow")
    try:
        cast(Any, queryset)._apply_scope_in_place()
    except MissingActorError:
        return cast(QuerySet[Any], queryset.none())
    return cast(QuerySet[Any], queryset)


# Aggregation is owned by ``strawberry-django-aggregates``: it emits the
# group-by surface (offset-paginated groups, multi-axis composite keys, the
# full granularity track, having, and ordering). Angee contributes only the
# REBAC-scoped queryset. Count is the M2 measure; ``word_count`` is the
# summable numeric column exposed to grouped and ungrouped aggregates.
#
# Group-by axes are non-gated read fields only. ``is_starred`` and
# ``reminder_at`` are owner-gated reads (``permissions.zed``: ``read__*``);
# exposing either as an axis would leak the owner-only value through the bucket
# keys/counts, because aggregation runs with field enforcement relaxed (see
# ``_rebac_scoped``).
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
    get_queryset=_rebac_scoped,
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

    return _rebac_scoped().filter(**Note._public_id_lookup(id.node_id)).first()


def _user_public_id(user_id: Any) -> strawberry.ID | None:
    """Return a user's opaque public id without fetching the user row."""

    if user_id is None:
        return None
    return strawberry.ID(public_id_of(User(id=user_id)))


schemas = {
    "public": {
        "query": [NotesQuery],
        "mutation": [crud(NoteType, create=NoteInput, update=NotePatch, delete=True)],
        "types": [NoteType, NoteRevision, *_AGGREGATE_TYPES],
    },
    "console": {
        "query": [NotesQuery],
        "mutation": [crud(NoteType, create=NoteInput, update=NotePatch, delete=True)],
        "subscription": [changes(Note, field="noteChanged")],
        "types": [NoteType, NoteRevision, *_AGGREGATE_TYPES],
    },
}
