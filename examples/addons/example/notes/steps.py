"""Note publication steps with an optional, independently assigned review."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.workflows.reviews import ReviewStep
from angee.workflows.steps import Done, Step


class NotePublicationOutput(BaseModel):
    """Safe note identity and lifecycle state emitted by publication steps."""

    id: str
    title: str
    status: str


class NoteReviewBasis(NotePublicationOutput):
    """The complete text and lifecycle state presented for one review round."""

    body: str


class ValidateNotePublication(Step[None, NotePublicationOutput, None]):
    """Check publication readiness through the note's own contract."""

    key = "note_validate_publication"
    label = "Validate note"
    category = "Activity"
    subject = "notes.note"
    outcomes = {"needs_review": "Needs review", "ok": "Ready"}

    def run(self, ctx: Any) -> Done:
        """Return the note's safe publication summary under the run actor."""

        note = ctx.subject
        return ctx.done(note.publication_summary(), outcome="needs_review" if note.reviewer_id else "ok")


class ApproveNote(Action, value="approve", label="Approve", verdict=Verdict.COMPLETED, outcome="approved"):
    """Approve publication with an optional retained explanation."""

    note: str = ""


class RejectNote(Action, value="reject", label="Reject", verdict=Verdict.REJECTED, outcome="rejected"):
    """Decline publication with an explanation for the author."""

    reason: str = Field(min_length=3)


class ReviewNotePublication(ReviewStep[NotePublicationOutput, NotePublicationOutput, None, NoteReviewBasis]):
    """Ask the assigned reader before the run actor publishes the note."""

    key = "note_review_publication"
    label = "Review note"
    category = "Activity"
    subject = "notes.note"
    kind = "note_publication"
    actions = (ApproveNote, RejectNote)

    def ask(self, ctx: Any) -> Any:
        """Freeze the summary and retain a link covered by standing reviewer access."""

        note = ctx.subject
        return ctx.ask(DecisionRequest(
            kind=self.kind, subject=note, assignees=(note.reviewer,), actions=self.actions,
            basis={**note.publication_summary(), "body": note.body},
            context=DecisionContext(references=(
                DecisionRecordReference(model="notes.note", id=note.sqid, label=note.title),
            )),
        ), policy="first")

    def apply(self, ctx: Any, settled: Any) -> Done:
        """Re-ask changed text before routing the answer under the run actor."""

        answer = settled[0]
        if answer.basis.body != ctx.subject_for_update().body:
            raise ValidationError({"body": "The note changed after review; review its body again."})
        return ctx.done(answer.basis, outcome=answer.action.outcome)


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
