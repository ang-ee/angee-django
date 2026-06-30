"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from django.db import models
from strawberry import auto

from angee.graphql.data import hasura_model_resource
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.revisions import revisions
from angee.graphql.subscriptions import changes
from angee.graphql.writes import write_queryset
from angee.iam.audit import AuthoredRefMixin

Note = apps.get_model("notes", "Note")


@strawberry_django.type(Note)
class NoteType(AuthoredRefMixin, AngeeNode):
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


def _note_queryset(info: strawberry.Info) -> models.QuerySet[Note]:
    """Return the actor-scoped note queryset for row reads."""

    del info
    return Note.objects.all()


def _note_aggregate_queryset(info: strawberry.Info) -> models.QuerySet[Note]:
    """Return the row-scoped queryset safe for aggregate/group math."""

    del info
    return Note.objects.all().scoped_for_aggregate()


@strawberry.type
class NoteDeletePreviewMutation:
    """Authored delete-preview operation for notes."""

    @strawberry.mutation(name="delete_note")
    def delete_note(self, id: PublicID, confirm: bool = False) -> DeletePreview:
        """Preview or confirm deletion of one note by public id."""

        return delete_by_public_id(
            Note,
            str(id),
            confirm=confirm,
            queryset=write_queryset(Note),
        )


NoteDeletePreviewMutation = attach_delete_preview_metadata(
    NoteDeletePreviewMutation,
    model=Note,
    node=NoteType,
    field="delete_note",
)


_NOTE_RESOURCE = hasura_model_resource(
    NoteType,
    model=Note,
    name="notes",
    filterable=["id", "title", "status", "tags", "is_starred", "updated_at"],
    sortable=["title", "status", "updated_at", "created_at", "word_count"],
    aggregatable=["id", "word_count"],
    groupable=["status", "tags", "updated_at"],
    writable=["title", "body", "status", "tags", "is_starred", "reminder_at"],
    get_queryset=_note_queryset,
    get_aggregate_queryset=_note_aggregate_queryset,
    id_column="sqid",
)


_NOTE_SCHEMA_BUCKET = {
    "query": [_NOTE_RESOURCE.query, revisions(NoteType)],
    "mutation": [_NOTE_RESOURCE.mutation, NoteDeletePreviewMutation],
    "types": [NoteType, *_NOTE_RESOURCE.types],
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
