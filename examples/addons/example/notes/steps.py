"""Concrete workflow operations owned by the example Notes addon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from pydantic import BaseModel
from rebac import actor_context, system_context, to_object_ref, to_subject_ref
from rebac.backends import backend as rebac_backend

from angee.workflows.steps import StepEffect, StepImpl, StepOutcome, StepResult


class NotePublicationOutput(BaseModel):
    """Safe Note identity and lifecycle state emitted to the workflow journal."""

    id: str
    title: str
    status: str


class NoteWorkflowStep(StepImpl):
    """Workflow operation base owning current Note subject and actor resolution."""

    deterministic = False
    output_model = NotePublicationOutput
    subject_declaration = "notes.note"

    def writable_note_subject(
        self,
        step_run: Any,
        *,
        actions: tuple[str, ...] = ("write",),
    ) -> tuple[Any, Any]:
        """Resolve the current durable subject and recheck the run creator's write access."""

        run = step_run.run
        owner_id = getattr(run, "created_by_id", None)
        if owner_id is None:
            raise ValidationError({"run": "Note workflow steps require a run creator."})
        user_model = get_user_model()
        note_model = apps.get_model("notes", "Note")
        try:
            with system_context(reason="notes.workflow.subject"):
                actor = user_model._base_manager.get(pk=owner_id)
                subject = run.subject
                if subject is not None and isinstance(subject, note_model):
                    subject = note_model._base_manager.get(pk=subject.pk)
        except user_model.DoesNotExist as error:
            raise ValidationError({"run": "Note workflow run creator was not found."}) from error
        except note_model.DoesNotExist as error:
            raise ValidationError({"subject": "Note workflow subject was not found."}) from error
        if subject is None or not isinstance(subject, note_model):
            raise ValidationError({"subject": "Note workflow steps require a notes.Note subject."})
        actor_ref = to_subject_ref(actor)
        resource_ref = to_object_ref(subject)
        for action in actions:
            allowed = rebac_backend().check_access(
                subject=actor_ref,
                action=action,
                resource=resource_ref,
            )
            if not allowed.allowed:
                raise ValidationError(
                    {"subject": "The run creator no longer has permission to write this note."}
                )
        return subject, actor


class NoteValidateForPublicationStep(NoteWorkflowStep):
    """Validate the current Note subject before requesting approval."""

    key = "note_validate_publication"
    label = "Validate note"
    category = "Activity"
    description = "Check that the current note is ready for publication review."
    outcomes = (
        StepOutcome("needs_review", "Needs review", "The note is ready for human approval."),
    )
    effect = StepEffect.READ
    effect_description = "Reads the current note and its publication readiness."
    idempotent = True

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Return a safe review summary for a currently writable Note."""

        del now
        note, _actor = self.writable_note_subject(step_run)
        return StepResult.done(output=note.publication_summary(), outcome="needs_review")


class NotePublishStep(NoteWorkflowStep):
    """Publish an approved Note through the Note lifecycle owner."""

    key = "note_publish"
    label = "Publish note"
    category = "Activity"
    description = "Publish an approved note that is still ready and writable."
    outcomes = (
        StepOutcome("published", "Published", "The note was moved to the active state."),
    )
    effect = StepEffect.WRITE
    effect_description = "Changes the current note from in review to active."
    idempotent = False

    def run(self, step_run: Any, *, now: datetime) -> StepResult:
        """Recheck access and atomically move an in-review Note to active."""

        del now
        note, actor = self.writable_note_subject(
            step_run,
            actions=("write", "write__status"),
        )
        with actor_context(to_subject_ref(actor)):
            output = note.publish()
        return StepResult.done(output=output, outcome="published")
