"""Settings fragments contributed by the example Notes addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.note_validate_publication": (
        "example.notes.steps.ValidateNotePublication"
    ),
    "ANGEE_WORKFLOW_STEP_CLASSES.note_publish": "example.notes.steps.PublishNote",
    "ANGEE_WORKFLOW_STEP_CLASSES.note_review_publication": "example.notes.steps.ReviewNotePublication",
    "ANGEE_WORKFLOW_STEP_CLASSES.note_collect_reviews": "example.notes.steps.CollectNoteReviews",
    "ANGEE_WORKFLOW_STEP_CLASSES.note_start_publication": "example.notes.steps.StartNotePublication",
    "ANGEE_WORKFLOW_STEP_CLASSES.note_publication_result": "example.notes.steps.NotePublicationResult",
}
