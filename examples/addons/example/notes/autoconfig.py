"""Settings fragments contributed by the example Notes addon."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_WORKFLOW_STEP_CLASSES.note_validate_publication": (
        "example.notes.steps.ValidateNotePublication"
    ),
    "ANGEE_WORKFLOW_STEP_CLASSES.note_publish": "example.notes.steps.PublishNote",
}
