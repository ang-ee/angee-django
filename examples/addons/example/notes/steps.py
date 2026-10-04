"""Note publication steps with an optional, independently assigned review."""

from __future__ import annotations

from collections import Counter
from typing import Any

from django.apps import apps
from pydantic import BaseModel

from angee.base.evidence import FactAuthority
from angee.decisions.contracts import DecisionContext, DecisionFact, DecisionProposal, DecisionRequest
from angee.workflows.awaits import AwaitRunInput
from angee.workflows.maps import MapItem
from angee.workflows.reviews import DecisionStep
from angee.workflows.steps import Done, EmptyOutput, Step, Wait


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


class ReviewNotePublication(DecisionStep[NotePublicationOutput, NotePublicationOutput, None]):
    """One question about the current note; edits do not create review rounds."""

    key = "note_review_publication"
    label = "Review note"
    category = "Activity"
    kind = "note_publication"
    outcomes = {"approved": "Approved", "rejected": "Rejected"}

    def ask(self, ctx: Any) -> Any:
        note = ctx.load(apps.get_model("notes", "Note"), ctx.input.id)
        return ctx.ask(
            DecisionRequest(
                kind=self.kind,
                records=(note,),
                assignees=(note.reviewer,),
                proposal=DecisionProposal.model_validate(
                    {
                        "alternatives": (
                            {"key": "approve", "label": "Approve the current note", "outcome": "approved"},
                            {"key": "reject", "label": "Reject publication", "outcome": "rejected"},
                        )
                    }
                ),
                context=DecisionContext(
                    facts=(
                        DecisionFact(
                            pointer="/body", label="Note body", value=note.body, authority=FactAuthority.SOURCE
                        ),
                    )
                ),
            )
        )

    def continue_with(self, ctx: Any, decision: Any, outcome: str) -> Done:
        note = ctx.load(apps.get_model("notes", "Note"), ctx.input.id)
        return ctx.done(note.publication_summary(), outcome=outcome)


class CollectNoteReviews(Step[list[MapItem[NotePublicationOutput]], dict[str, int], None]):
    """Count each mapped review's answered outcome."""

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
