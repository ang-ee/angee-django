"""The shipped note graph reviews and publishes through composed owners."""

from __future__ import annotations

from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.jobs.enqueue import celery_app
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind
from angee.workflows.testing.drivers import load_workflow, register_steps, run_until, start_run
from example.notes.steps import NotePublicationOutput, PublishNote, ReviewNotePublication, ValidateNotePublication

Note = apps.get_model("notes", "Note")
StepRun = apps.get_model("workflows", "StepRun")
Resource = apps.get_model("resources", "Resource")
Decision = apps.get_model("decisions", "Decision")
User = get_user_model()


class ClassLabelNotePublication(ValidateNotePublication):
    """Contribute a class-form subject label through the normal step registry."""

    key = "test_note_subject_label"
    subject = "notes.Note"


class NoteWorkflowStepTests(TransactionTestCase):
    """Prove resource installation, actor scope, domain validation and audit."""

    def setUp(self) -> None:
        """Install the shipped document with captured commit-time transport."""

        patcher = patch.object(celery_app, "send_task")
        self.sent = patcher.start()
        self.addCleanup(patcher.stop)
        call_command("rebac", "sync", verbosity=0)
        with system_context(reason="note workflow fixtures"):
            self.owner = User.objects.create_user(username="note-owner")
            self.other = User.objects.create_user(username="note-other")
            self.reviewer = User.objects.create_user(username="note-reviewer")
            self.admin = User.objects.create_user(username="note-admin")
            grant_role(actor=self.admin, role="angee/role:admin")
        self.workflow = load_workflow("example.notes.note_publish", actor=self.admin, allow_non_dev=True)
        self.workflow.with_actor(self.admin).grant_record_access("starter", self.owner)

    def note(self, **kwargs):
        """Create a subject owned by the non-administrator execution actor."""

        values = {
            "title": "Release notes", "body": "Ready for readers.",
            "status": Note.Status.IN_REVIEW, "reviewer": self.reviewer,
        }
        values.update(kwargs)
        with system_context(reason="note workflow subject fixture"):
            return Note.objects.create(created_by=self.owner, **values)

    def answer_review(self, run, *, action="approve", values=None):
        """Answer a real waiting decision through its revision-checked owner."""

        run_until(run)
        with system_context(reason="note review seat assertion"):
            step_run = StepRun.objects.get(run=run, node_key="review")
            self.assertEqual(step_run.status, StepRunStatus.WAITING)
            self.assertEqual(step_run.waiting_kind, WaitingKind.DECISION)
            decision = Decision.objects.get(group=step_run.decision_group)
            self.assertEqual(decision.requester_id, self.owner.pk)
            self.assertEqual(list(decision.assignees.values_list("pk", flat=True)), [self.reviewer.pk])
            self.assertEqual(decision.basis["body"], "Ready for readers.")
        return Decision.objects.decide(
            decision.pk, actor=self.reviewer, revision=decision.revision,
            action=action, values=values or {},
        )

    def test_shipped_graph_validates_and_publishes_with_actor_audit(self) -> None:
        """A separate reviewer answers; publication audit still records the run actor."""

        note = self.note()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        self.answer_review(run)
        run_until(run)
        with system_context(reason="note workflow result assertions"):
            note.refresh_from_db()
            step_runs = list(StepRun.objects.filter(run=run).order_by("created_at", "pk"))
            self.assertEqual([step_run.node_key for step_run in step_runs], ["validate", "review", "publish"])
            self.assertEqual([step_run.outcome for step_run in step_runs], ["needs_review", "approved", "published"])
            self.assertEqual(
                step_runs[0].output,
                {"id": note.sqid, "title": note.title, "status": Note.Status.IN_REVIEW},
            )
            self.assertEqual(run.status, RunStatus.SUCCEEDED)
            self.assertEqual(run.outcome, "published")
            self.assertEqual(run.output, {"id": note.sqid, "title": note.title, "status": Note.Status.ACTIVE})
            self.assertEqual(note.status, Note.Status.ACTIVE)
            self.assertEqual(note.updated_by_id, self.owner.pk)
            self.assertEqual(note.history.count(), 2)
            self.assertEqual(note.history.first().status, Note.Status.ACTIVE)
        self.assertGreaterEqual(self.sent.call_count, 4)

    def test_rejection_preserves_the_note_and_returns_the_review_result(self) -> None:
        """A rejected answer ends the branch without any publication write."""

        note = self.note()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        answered = self.answer_review(run, action="reject", values={"reason": "Needs clearer wording"})
        run_until(run)
        with system_context(reason="note rejection assertions"):
            note.refresh_from_db()
            self.assertEqual(answered.resolved_by_id, self.reviewer.pk)
            self.assertEqual(run.status, RunStatus.SUCCEEDED)
            self.assertEqual(run.outcome, "rejected")
            self.assertEqual(run.output["id"], note.sqid)
            self.assertEqual(note.status, Note.Status.IN_REVIEW)
            self.assertEqual(note.history.count(), 1)
            self.assertEqual(StepRun.objects.get(run=run, node_key="publish").status, StepRunStatus.SKIPPED)

    def test_changed_body_reasks_and_preserves_the_answered_round(self) -> None:
        """A stale body cannot proceed to publication even after approval."""

        note = self.note()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        answered = self.answer_review(run)
        note.body = "Revised text that the reviewer has not approved."
        note.with_actor(self.owner).save(update_fields={"body"})
        run_until(run)
        with system_context(reason="changed note review assertions"):
            note.refresh_from_db()
            step = StepRun.objects.get(run=run, node_key="review")
            replacement = Decision.objects.get(group_id=step.decision_group_id)
            answered.refresh_from_db()
            self.assertEqual(step.status, StepRunStatus.WAITING)
            self.assertEqual(step.waiting_kind, WaitingKind.DECISION)
            self.assertEqual(step.state["review_round"], 2)
            self.assertNotEqual(replacement.group_id, answered.group_id)
            self.assertIn("body", replacement.errors)
            self.assertEqual(answered.resolution["action"], "approve")
            self.assertIsNotNone(answered.group.settled_at)
            self.assertEqual(note.status, Note.Status.IN_REVIEW)
            self.assertFalse(StepRun.objects.filter(run=run, node_key="publish").exists())

    def test_author_cannot_be_the_note_reviewer(self) -> None:
        """Publication readiness rejects a seat its requester could not answer."""

        note = self.note(reviewer=self.owner)
        with self.assertRaisesMessage(ValidationError, "someone other than the note's author"):
            note.publication_summary()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        run_until(run)
        self.assertEqual(run.status, RunStatus.FAILED)
        with system_context(reason="invalid note reviewer assertions"):
            self.assertFalse(Decision.objects.exists())
            self.assertIn("reviewer", StepRun.objects.get(run=run, node_key="validate").attempts.get().error)

    def test_reviewer_has_standing_read_but_cannot_publish(self) -> None:
        """The note assignment grants evidence access before admission, without tuples."""

        note = self.note()
        note.require_access("read", actor=self.reviewer)
        with self.assertRaises(PermissionDenied):
            note.require_access("write", actor=self.reviewer)
        self.assertEqual(note.with_actor(self.owner).direct_record_access(), ())
        run = start_run(self.workflow, actor=self.owner, subject=note)
        self.answer_review(run)
        self.assertEqual(note.with_actor(self.owner).direct_record_access(), ())

    def test_unassigned_note_uses_the_direct_publication_branch(self) -> None:
        """The graph explicitly skips review when no person is assigned."""

        note = self.note(reviewer=None)
        run = start_run(self.workflow, actor=self.owner, subject=note)
        run_until(run)
        with system_context(reason="unassigned note publication assertions"):
            self.assertEqual(run.outcome, "published")
            self.assertEqual(StepRun.objects.get(run=run, node_key="validate").outcome, "ok")
            self.assertEqual(StepRun.objects.get(run=run, node_key="review").status, StepRunStatus.SKIPPED)
            self.assertFalse(Decision.objects.exists())

    def test_duplicate_delivery_keeps_publication_history_unchanged(self) -> None:
        """A stale delivery after publication does not write the subject again."""

        note = self.note()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        self.answer_review(run)
        run_until(run)
        with system_context(reason="note publication stale delivery fixture"):
            step_run = StepRun.objects.get(run=run, node_key="publish")
            history_count = note.history.count()
        self.assertFalse(StepRun.objects.execute(step_run.pk))
        with system_context(reason="note publication stale delivery assertion"):
            self.assertEqual(note.history.count(), history_count)

    def test_wrong_subject_model_is_rejected_at_admission(self) -> None:
        """The declared subject contract rejects a different model before execution."""

        with self.assertRaises(ValidationError):
            start_run(self.workflow, actor=self.owner, subject=self.owner)

    def test_step_subject_model_label_is_canonicalized_for_publication(self) -> None:
        """A step's Django class label agrees with the canonical workflow subject."""

        with register_steps(ClassLabelNotePublication):
            workflow = type(self.workflow).objects.install_definition(
                key="canonical-note-subject",
                name="Canonical note subject",
                subject_model="notes.note",
                draft={"nodes": {"validate": {"step": ClassLabelNotePublication.key}}},
                actor=self.admin,
            )
            self.assertIsNotNone(workflow.published_id)
            self.assertEqual(workflow.subject_model, "notes.note")
            self.assertEqual(workflow.published.definition.step("validate").subject, "notes.note")

    def test_permission_removed_after_launch_prevents_publication(self) -> None:
        """An actor who loses access cannot read or modify the note in a later step."""

        note = self.note()
        run = start_run(self.workflow, actor=self.owner, subject=note)
        self.answer_review(run)
        run_until(run, node="publish")
        with system_context(reason="revoke note workflow subject owner"):
            note.created_by = self.other
            note.save(update_fields={"created_by"})
        run_until(run)
        self.assertEqual(run.status, RunStatus.FAILED)
        with system_context(reason="note workflow permission assertions"):
            note.refresh_from_db()
            step_run = StepRun.objects.get(run=run, node_key="publish")
            self.assertEqual(step_run.status, StepRunStatus.FAILED)
            self.assertTrue(step_run.attempts.get().error)
            self.assertEqual(note.status, Note.Status.IN_REVIEW)

    def test_invalid_content_and_state_leave_note_unchanged(self) -> None:
        """Domain readiness failures are durable failures without publication writes."""

        for fields, message in (
            ({"title": "", "body": "Ready", "status": Note.Status.IN_REVIEW}, "title"),
            ({"title": "Draft", "body": "", "status": Note.Status.IN_REVIEW}, "content"),
            ({"title": "Draft", "body": "Ready", "status": Note.Status.DRAFT}, "in review"),
        ):
            with self.subTest(message=message):
                note = self.note(**fields)
                run = start_run(self.workflow, actor=self.owner, subject=note)
                run_until(run)
                self.assertEqual(run.status, RunStatus.FAILED)
                with system_context(reason="note workflow invalid subject assertions"):
                    note.refresh_from_db()
                    step_run = StepRun.objects.get(run=run, node_key="validate")
                    self.assertIn(message, step_run.attempts.get().error)
                    self.assertEqual(note.status, fields["status"])
                    self.assertEqual(note.history.count(), 1)

    def test_resource_loader_installs_the_same_document(self) -> None:
        """Native resource loading keeps one graph row and immutable publication."""

        call_command("resources", "load", include_demo=True, allow_non_dev=True, verbosity=0)
        with system_context(reason="note workflow resource assertion"):
            installed = Resource.objects.get(source_addon="example.notes", xref="note_publish").target_instance()
            self.assertEqual(installed.pk, self.workflow.pk)
            self.assertEqual(installed.published_id, self.workflow.published_id)
            self.assertEqual(installed.subject_model, "notes.note")
            self.assertEqual(set(installed.published.definition.nodes), {"validate", "review", "publish"})

    def test_steps_declare_their_typed_contract(self) -> None:
        """The registry classes own schemas, subjects and named outcomes."""

        for step in (ValidateNotePublication, ReviewNotePublication, PublishNote):
            self.assertEqual(step.output_schema(), NotePublicationOutput.model_json_schema())
            self.assertEqual(step.subject, "notes.note")
        self.assertEqual(ValidateNotePublication.available_outcomes(None), {
            "needs_review": "Needs review", "ok": "Ready", "error": "Error",
        })
        self.assertEqual(set(ReviewNotePublication.available_outcomes(None)), {
            "approved", "rejected", "expired", "superseded", "error",
        })
        self.assertEqual(PublishNote.available_outcomes(None), {"published": "Published", "error": "Error"})
