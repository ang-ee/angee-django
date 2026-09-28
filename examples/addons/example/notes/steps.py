"""Note publication steps; human review is added with the decisions layer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from angee.workflows.steps import Done, Step


class NotePublicationOutput(BaseModel):
    """Safe note identity and lifecycle state emitted by publication steps."""

    id: str
    title: str
    status: str


class ValidateNotePublication(Step[None, NotePublicationOutput, None]):
    """Check publication readiness through the note's own contract."""

    key = "note_validate_publication"
    label = "Validate note"
    category = "Activity"
    subject = "notes.note"
    outcomes = {"needs_review": "Needs review"}

    def run(self, ctx: Any) -> Done:
        """Return the note's safe publication summary under the run actor."""

        return ctx.done(ctx.subject.publication_summary(), outcome="needs_review")


class PublishNote(Step[NotePublicationOutput, NotePublicationOutput, None]):
    """Publish a ready note through its audited lifecycle owner."""

    key = "note_publish"
    label = "Publish note"
    category = "Activity"
    subject = "notes.note"
    outcomes = {"published": "Published"}

    def run(self, ctx: Any) -> Done:
        """Lock the current note and publish through its actor-scoped save path."""

        note = ctx.subject_for_update()
        return ctx.done(note.publish(), outcome="published")
