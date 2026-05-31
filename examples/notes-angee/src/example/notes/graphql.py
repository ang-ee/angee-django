"""Manual Strawberry schema contributions for notes."""

from __future__ import annotations

import strawberry
import strawberry_django
from django.apps import apps
from strawberry import auto

from angee.base.graphql import changes, crud

Note = apps.get_model("notes", "Note")


@strawberry_django.type(Note)
class NoteType:
    """GraphQL projection of a note."""

    title: auto
    body: auto
    status: auto
    tags: auto
    is_starred: auto
    created_at: auto
    updated_at: auto

    @strawberry.field
    def sqid(self) -> str:
        """Return the bare public id."""

        return self.public_id

    @strawberry.field
    def id(self) -> strawberry.ID:
        """Return the public opaque id."""

        return strawberry.ID(self.public_id)

    @strawberry.field
    def word_count(self) -> int:
        """Return the computed word count."""

        return self.word_count


@strawberry.input
class NoteInput:
    """Fields accepted when creating a note."""

    title: str
    body: str = ""
    status: str = "draft"
    tags: list[str] = strawberry.field(default_factory=list)
    is_starred: bool = False


@strawberry.input
class NotePatch:
    """Fields accepted when updating a note."""

    title: str | None = None
    body: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    is_starred: bool | None = None


@strawberry.type
class NotesQuery:
    """Public notes queries."""

    @strawberry.field
    def notes(self) -> list[NoteType]:
        """Return notes in model order."""

        return list(Note.objects.all())

    @strawberry.field
    def note(self, id: strawberry.ID) -> NoteType | None:
        """Return one note by public id."""

        return Note.from_public_id(str(id))


schemas = {
    "public": {
        "query": [NotesQuery],
        "mutation": [
            crud(NoteType, create=NoteInput, update=NotePatch, delete=True)
        ],
        "types": [NoteType],
    },
    "console": {
        "query": [NotesQuery],
        "mutation": [
            crud(NoteType, create=NoteInput, update=NotePatch, delete=True)
        ],
        "subscription": [changes(Note, field="noteChanged")],
        "types": [NoteType],
    },
}
