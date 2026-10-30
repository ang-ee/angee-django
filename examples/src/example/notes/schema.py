"""Strawberry-Django schema contributions for notes."""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.graphql.capabilities import permissions_field
from angee.graphql.data.contracts import DataResourceSubtitleMetadata
from angee.graphql.data.hasura import AngeeHasuraWriteBackend, hasura_model_resource
from angee.graphql.deletion import DeletePreview, attach_delete_preview_metadata, delete_by_public_id
from angee.graphql.ids import PublicID
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.audit import AuthoredRefMixin
from angee.iam.schema import UserType

Note = apps.get_model("notes", "Note")


@strawberry_django.type(Note)
class NoteType(AuthoredRefMixin, AngeeNode):
    """GraphQL projection of a note."""

    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name, only=["title"], description=NODE_DISPLAY_NAME_DESCRIPTION
    )
    title: auto
    body: auto
    status: auto
    tags: auto
    is_starred: bool | None
    reminder_at: auto
    created_at: auto
    updated_at: auto
    word_count: auto
    reviewer: UserType | None
    permissions = permissions_field(("write", "share", "delete"))


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
            queryset=Note.objects.for_write(),
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
    filterable=["id", "title", "status", "tags", "updated_at"],
    sortable=["title", "status", "updated_at", "created_at", "word_count"],
    aggregatable=["id", "word_count"],
    groupable=["status", "tags", "updated_at"],
    writable=["title", "body", "status", "tags", "is_starred", "reminder_at", "reviewer"],
    write_backend=AngeeHasuraWriteBackend(Note, public_id_fields=("reviewer",)),
    id_column="sqid",
    subtitle=DataResourceSubtitleMetadata(word_count="word_count"),
)


_NOTE_SCHEMA_BUCKET = {
    "query": [_NOTE_RESOURCE.query],
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
