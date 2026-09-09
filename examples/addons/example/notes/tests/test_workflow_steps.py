"""Executable Note workflow operations against composed models and permissions."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.workflows import engine
from angee.workflows.attempts import RecoveryMode
from angee.workflows.steps import StepEffect
from example.notes.steps import (
    NotePublicationOutput,
    NotePublishStep,
    NoteValidateForPublicationStep,
)

Note = apps.get_model("notes", "Note")
Workflow = apps.get_model("workflows", "Workflow")
Step = apps.get_model("workflows", "Step")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
StepRun = apps.get_model("workflows", "StepRun")
WorkflowDispatch = apps.get_model("workflows", "WorkflowDispatch")
Resource = apps.get_model("resources", "Resource")
User = get_user_model()


def execute_retained(step_run: Any) -> None:
    """Consume the exact execution intent allocated for one started row."""

    with system_context(reason="note workflow retained execution"):
        step_run.refresh_from_db()
        attempt = step_run.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)


class NoteWorkflowStepTests(TransactionTestCase):
    """Exercise the Note-owned readiness and publication effect."""

    def setUp(self) -> None:
        for enqueue_name in (
            "enqueue_advance",
            "enqueue_advance_at",
            "enqueue_execute",
            "enqueue_decision_escalation_at",
            "enqueue_decision_expiry_at",
        ):
            patcher = patch.object(engine, enqueue_name)
            patcher.start()
            self.addCleanup(patcher.stop)
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="note workflow test setup"):
            self.owner = User.objects.create_user(username="note-workflow-owner")
            self.other = User.objects.create_user(username="note-workflow-other")
            self.workflow = Workflow.objects.create(name="Note publication")
            self.step = Step.objects.create(
                workflow=self.workflow,
                key="validate",
                name="Validate note",
                step_class="note_validate_publication",
                is_entry=True,
            )

    def step_run(self, subject: object, *, creator: object | None = None):
        with system_context(reason="note workflow test run"):
            run = WorkflowRun.objects.create(
                workflow=self.workflow,
                subject=subject,
                created_by=creator if creator is not None else self.owner,
            )
            return StepRun.objects.create(run=run, step=self.step)

    def test_valid_note_is_summarized_and_published_with_actor_audit(self) -> None:
        with system_context(reason="note workflow test note"):
            note = Note.objects.create(
                title="Release notes",
                body="Ready for readers.",
                status=Note.Status.IN_REVIEW,
                created_by=self.owner,
            )
        step_run = self.step_run(note)

        validated = NoteValidateForPublicationStep().run(step_run, now=datetime.now(UTC))
        self.assertEqual(validated.outcome, "needs_review")
        self.assertEqual(
            validated.output,
            {"id": str(note.sqid), "title": "Release notes", "status": Note.Status.IN_REVIEW},
        )

        published = NotePublishStep().run(step_run, now=datetime.now(UTC))
        note.refresh_from_db()
        self.assertEqual(published.outcome, "published")
        self.assertEqual(published.output["status"], Note.Status.ACTIVE)
        self.assertEqual(note.status, Note.Status.ACTIVE)
        self.assertEqual(note.updated_by_id, self.owner.pk)
        self.assertEqual(note.history.count(), 2)
        self.assertEqual(note.history.first().status, Note.Status.ACTIVE)

    def test_wrong_model_subject_is_rejected(self) -> None:
        step_run = self.step_run(self.other)
        with self.assertRaisesMessage(ValidationError, "notes.Note subject"):
            NoteValidateForPublicationStep().run(step_run, now=datetime.now(UTC))

    def test_publish_recovery_reconciles_an_already_active_note_without_another_write(self) -> None:
        with system_context(reason="note workflow recovery fixture"):
            note = Note.objects.create(
                title="Already published",
                body="Retained body.",
                status=Note.Status.ACTIVE,
                created_by=self.owner,
            )
        step_run = self.step_run(note)
        history_count = note.history.count()

        recovered = NotePublishStep().run_recovery(
            step_run,
            now=datetime.now(UTC),
            source_attempt=object(),
            mode=RecoveryMode.RECONCILE,
        )

        note.refresh_from_db()
        self.assertEqual(recovered.outcome, "published")
        self.assertEqual(recovered.output["status"], Note.Status.ACTIVE)
        self.assertEqual(recovered.artifacts[0].target, note)
        self.assertEqual(note.history.count(), history_count)

    def test_missing_run_creator_is_rejected(self) -> None:
        with system_context(reason="note workflow test note"):
            note = Note.objects.create(
                title="No actor",
                body="Ready.",
                status=Note.Status.IN_REVIEW,
                created_by=self.owner,
            )
        step_run = self.step_run(note)
        with system_context(reason="remove note workflow run creator"):
            WorkflowRun.objects.filter(pk=step_run.run_id).update(created_by=None)
        step_run.run.refresh_from_db()

        with self.assertRaisesMessage(ValidationError, "require a run creator"):
            NoteValidateForPublicationStep().run(step_run, now=datetime.now(UTC))

    def test_permission_removed_after_launch_is_rejected(self) -> None:
        with system_context(reason="note workflow test note"):
            note = Note.objects.create(
                title="Revoked",
                body="Was ready.",
                status=Note.Status.IN_REVIEW,
                created_by=self.owner,
            )
        step_run = self.step_run(note)
        with system_context(reason="revoke note workflow owner"):
            note.created_by = self.other
            note.save(update_fields={"created_by"})

        with self.assertRaisesMessage(ValidationError, "no longer has permission"):
            NotePublishStep().run(step_run, now=datetime.now(UTC))
        note.refresh_from_db()
        self.assertEqual(note.status, Note.Status.IN_REVIEW)

    def test_invalid_content_and_state_leave_note_unchanged(self) -> None:
        for fields, message in (
            ({"title": "", "body": "Ready", "status": Note.Status.IN_REVIEW}, "title"),
            ({"title": "Draft", "body": "", "status": Note.Status.IN_REVIEW}, "content"),
            ({"title": "Draft", "body": "Ready", "status": Note.Status.DRAFT}, "in review"),
        ):
            with self.subTest(message=message), system_context(reason="note workflow invalid note"):
                note = Note.objects.create(created_by=self.owner, **fields)
            step_run = self.step_run(note)
            with self.assertRaisesMessage(ValidationError, message):
                NotePublishStep().run(step_run, now=datetime.now(UTC))
            note.refresh_from_db()
            self.assertEqual(note.status, fields["status"])

    def test_demo_resource_identity_uses_concrete_note_operations(self) -> None:
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="note workflow resource assertion"):
            workflow = Resource.objects.get(
                source_addon="example.notes",
                xref="note_publish_approval",
            ).target_instance()
            self.assertIsInstance(workflow, Workflow)
            steps = {step.key: step for step in workflow.steps.all()}
        self.assertEqual(workflow.subject_declaration, "notes.note")
        self.assertEqual(steps["entry"].step_class, "note_validate_publication")
        self.assertEqual(steps["finalize"].step_class, "note_publish")

    def test_operations_declare_their_authoring_contract(self) -> None:
        validate = NoteValidateForPublicationStep.operation(key="note_validate_publication")
        publish = NotePublishStep.operation(key="note_publish")

        self.assertEqual(validate.output_schema, NotePublicationOutput.model_json_schema())
        self.assertEqual(validate.subject_declaration, "notes.note")
        self.assertEqual(validate.effect, StepEffect.READ)
        self.assertTrue(validate.idempotent)
        self.assertEqual([outcome.key for outcome in validate.outcomes], ["needs_review"])
        self.assertEqual(publish.effect, StepEffect.WRITE)
        self.assertFalse(publish.idempotent)
        self.assertEqual([outcome.key for outcome in publish.outcomes], ["published"])

    def test_demo_graph_executes_validation_approval_and_publication(self) -> None:
        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="note workflow engine setup"):
            grant(actor=self.owner, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
            note = Note.objects.create(
                title="Engine release",
                body="Approved through the executable graph.",
                status=Note.Status.IN_REVIEW,
                created_by=self.owner,
            )
            draft = Resource.objects.get(
                source_addon="example.notes",
                xref="note_publish_approval",
            ).target_instance()
            workflow = Workflow.objects.current_published_for(draft)

        self.assertIsNotNone(workflow)
        run = engine.start(workflow, subject=note, actor=self.owner)
        engine.advance(run.pk)
        with system_context(reason="note workflow execute validation"):
            validation = StepRun.objects.get(run=run, step__key="entry")
        execute_retained(validation)
        validation.refresh_from_db()
        self.assertEqual(validation.outcome, "needs_review")

        engine.advance(run.pk)
        with system_context(reason="note workflow execute approval"):
            approval = StepRun.objects.get(run=run, step__key="approval")
        execute_retained(approval)
        with system_context(reason="note workflow read approval"):
            decision = approval.decisions.get()
        engine.decide(decision, "complete", actor=self.owner)

        engine.advance(run.pk)
        with system_context(reason="note workflow execute publication"):
            publication = StepRun.objects.get(run=run, step__key="finalize")
        execute_retained(publication)
        engine.advance(run.pk)

        run.refresh_from_db()
        publication.refresh_from_db()
        note.refresh_from_db()
        self.assertEqual(run.status, "succeeded")
        self.assertEqual(publication.outcome, "published")
        self.assertEqual(note.status, Note.Status.ACTIVE)
        self.assertEqual(note.updated_by_id, self.owner.pk)
