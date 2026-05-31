"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.db.models import QuerySet
from rebac import current_actor
from strawberry import auto, relay
from strawberry_django_aggregates import AggregateBuilder

from angee.base.graphql import AngeeNode, OffsetPaginated, changes, crud

Note = apps.get_model("notes", "Note")

# Register the model's TextChoices as the one GraphQL enum, named for the addon
# so the wire type does not collide with another addon's ``Status``. This
# single registration is what ``status: auto`` on NoteType resolves to, so the
# type, inputs, filter, and aggregate bucket all share one ``NoteStatus``.
NoteStatus = strawberry.enum(Note.Status, name="NoteStatus")


@strawberry_django.type(Note)
class NoteType(AngeeNode):
    """GraphQL projection of a note."""

    title: auto
    body: auto
    status: auto
    tags: auto
    is_starred: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["body"])
    def word_count(self) -> int:
        """Return the computed word count."""

        return cast(int, self.word_count)


@strawberry.input
class NoteInput:
    """Fields accepted when creating a note."""

    title: str
    body: str = ""
    status: NoteStatus = NoteStatus.DRAFT
    tags: list[str] = strawberry.field(default_factory=list)
    is_starred: bool = False


@strawberry.input
class NotePatch:
    """Fields accepted when updating a note."""

    id: relay.GlobalID
    title: str | None = strawberry.UNSET
    body: str | None = strawberry.UNSET
    status: NoteStatus | None = strawberry.UNSET
    tags: list[str] | None = strawberry.UNSET
    is_starred: bool | None = strawberry.UNSET


@strawberry_django.filter_type(Note, lookups=True)
class NoteFilter:
    """Field lookups accepted when filtering the notes connection."""

    status: auto
    is_starred: auto
    title: auto


@strawberry_django.order_type(Note)
class NoteOrder:
    """Orderings accepted by the notes connection."""

    title: auto
    status: auto
    updated_at: auto
    created_at: auto


def _rebac_scoped(info: strawberry.Info) -> QuerySet[Any]:
    """Return notes with the ambient REBAC actor eagerly applied.

    The aggregates library owns aggregation but expects a pre-scoped
    queryset (its one host-agnostic seam); this hook is the only Angee
    glue. Scope must be applied eagerly: ``compute_aggregation`` runs
    ``.values().annotate()`` paths that bypass ``RebacQuerySet._fetch_all``,
    where row scoping would otherwise fire. Under REBAC strict mode an
    unscoped queryset would raise ``MissingActorError`` at materialisation,
    so a missing actor yields an empty result rather than a leak.
    """

    actor = current_actor()
    queryset = Note.objects.all()
    if actor is None:
        return cast(QuerySet[Any], queryset.none())
    queryset = queryset.with_actor(actor).on_field_deny("allow")
    cast(Any, queryset)._apply_scope_in_place()
    return cast(QuerySet[Any], queryset)


# Aggregation is owned by ``strawberry-django-aggregates``: it emits the
# group-by surface (offset-paginated groups, multi-axis composite keys, the
# full granularity track, having, and ordering). Angee contributes only the
# REBAC-scoped queryset. Count is the M2 measure (notes carry no summable
# numeric column; ``word_count`` is a Python property).
_note_aggregates = AggregateBuilder(
    model=Note,
    aggregate_fields=["id"],
    group_by_fields=["status", "is_starred", "updated_at"],
    pagination_style="offset",
    get_queryset=_rebac_scoped,
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

schemas = {
    "public": {
        "query": [NotesQuery],
        "mutation": [
            crud(NoteType, create=NoteInput, update=NotePatch, delete=True)
        ],
        "types": [NoteType, *_AGGREGATE_TYPES],
    },
    "console": {
        "query": [NotesQuery],
        "mutation": [
            crud(NoteType, create=NoteInput, update=NotePatch, delete=True)
        ],
        "subscription": [changes(Note, field="noteChanged")],
        "types": [NoteType, *_AGGREGATE_TYPES],
    },
}
