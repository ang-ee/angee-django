"""Note publication steps with an optional, independently assigned review."""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, Field

from angee.decisions.contracts import DecisionContext, DecisionRecordReference, DecisionRequest
from angee.decisions.forms import Action
from angee.decisions.states import Verdict
from angee.workflows.awaits import AwaitRunInput
from angee.workflows.maps import MapItem
from angee.workflows.reviews import ReviewStep
from angee.workflows.steps import Done, EmptyOutput, Step, Wait


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


class AwaitNoteReview(Step[None, NotePublicationOutput, None]):
    """Observe note saves until its owner moves it into review."""

    key = "note_await_review"
    label = "Await note review"
    category = "Activity"
    subject = "notes.note"

    def run(self, ctx: Any) -> Done | Wait:
        """Lock the predicate and watch registration in the same body transaction."""
        note = ctx.subject_for_update()
        if note.status == note.Status.IN_REVIEW:
            return ctx.done(note.publication_summary())
        ctx.watch(note)
        return ctx.wait()


class ApproveNote(Action, key="approve", label="Approve", verdict=Verdict.COMPLETED, outcome="approved"):
    """Approve publication with an optional retained explanation."""

    note: str = ""


class RejectNote(Action, key="reject", label="Reject", verdict=Verdict.REJECTED, outcome="rejected"):
    """Decline publication with an explanation for the author."""

    reason: str = Field(min_length=3)


class ReviewNotePublication(ReviewStep[NotePublicationOutput, NotePublicationOutput, None, NoteReviewBasis]):
    """Review the input note, independently of a containing run's subject."""

    key = "note_review_publication"
    label = "Review note"
    category = "Activity"
    kind = "note_publication"
    actions = (ApproveNote, RejectNote)

    def ask(self, ctx: Any) -> Any:
        """Freeze the summary and retain a link covered by standing reviewer access."""

        note = ctx.load(apps.get_model("notes", "Note"), ctx.input.id)
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
        note = ctx.load(apps.get_model("notes", "Note"), ctx.input.id, lock=True)
        if answer.basis.body != note.body:
            raise ValidationError({"body": "The note changed after review; review its body again."})
        return ctx.done(answer.basis, outcome=answer.action.outcome)


class CollectNoteReviews(Step[list[MapItem[NotePublicationOutput | EmptyOutput]], dict[str, int], None]):
    """Count each mapped review's outcome, including failed or unanswered items."""

    key = "note_collect_reviews"
    label = "Summarize note reviews"
    category = "Activity"

    def run(self, ctx: Any) -> Done:
        """Consume the map owner's typed, ordered results without reloading notes."""
        return ctx.done(dict(Counter(item.outcome for item in ctx.input)))


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


class StartNotePublication(Step[None, AwaitRunInput, None]):
    """Start one owned publication through the workflow context's admission owner."""

    key = "note_start_publication"
    label = "Start note publication"
    category = "Activity"
    subject = "notes.note"

    def run(self, ctx: Any) -> Done:
        """Keep the note and actor, using the step's derived child request key."""

        workflow = apps.get_model("workflows", "Workflow").objects.with_actor(ctx.actor).get(key="note-publish")
        child = ctx.start_run(workflow, subject=ctx.subject, relation="owned")
        return ctx.done({"run_id": child.sqid})


class NotePublicationResult(Step[None, NotePublicationOutput | EmptyOutput, None]):
    """Finish the parent with the child's safe result, including empty terminal output."""

    key = "note_publication_result"
    label = "Note publication result"
    category = "Activity"

    def run(self, ctx: Any) -> Done:
        """Preserve the child output; its exact outcome remains on the await step."""

        return ctx.done(ctx.input)
