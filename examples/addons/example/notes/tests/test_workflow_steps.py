"""The shipped note graph reviews and publishes through composed owners."""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TransactionTestCase
from rebac import system_context
from rebac.roles import grant as grant_role

from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus, WaitingKind
from angee.workflows.testing.drivers import capture_tasks, load_workflow, register_steps, run_until, start_run
from example.notes.steps import NotePublicationOutput, PublishNote, ReviewNotePublication, ValidateNotePublication

Note = apps.get_model("notes", "Note")
StepRun = apps.get_model("workflows", "StepRun")
StepWatch = apps.get_model("workflows", "StepWatch")
WorkflowRun = apps.get_model("workflows", "WorkflowRun")
Trigger = apps.get_model("workflows", "Trigger")
TriggerEvent = apps.get_model("workflows", "TriggerEvent")
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

        capture = capture_tasks()
        self.sent = capture.__enter__()
        self.addCleanup(capture.__exit__, None, None, None)
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

    def load_trigger(self):
        """Install the example's disabled policy through its declared resource."""
        return Resource.objects.load_xref(
            "example.notes.note_review_trigger", model=Trigger, actor=self.admin, allow_non_dev=True,
        )

    def test_watch_wait_resumes_after_note_save_and_rechecks_the_locked_predicate(self) -> None:
        """The shipped watcher re-arms for irrelevant saves and completes on review."""
        workflow = load_workflow("example.notes.note_await_review", actor=self.admin, allow_non_dev=True)
        workflow.with_actor(self.admin).grant_record_access("starter", self.owner)
        note = self.note(status=Note.Status.DRAFT)
        run = start_run(workflow, actor=self.owner, subject=note)
        run_until(run)
        with system_context(reason="note record watch assertions"):
            step = StepRun.objects.get(run=run)
            self.assertEqual((step.status, step.waiting_kind), (StepRunStatus.WAITING, WaitingKind.RECORD))
            self.assertIsNone(step.wake_at)
            self.assertEqual(StepWatch.objects.get(step_run=step).record_ref.public_id, note.sqid)

        note.title = "Ready for another look"
        note.with_actor(self.owner).save()
        self.assertEqual(runner.wake_records(), 1)
        run_until(run)
        self.assertEqual(run.status, RunStatus.WAITING)
        with system_context(reason="note re-armed watch assertions"):
            self.assertEqual(StepWatch.objects.filter(step_run=step).count(), 1)

        note.status = Note.Status.IN_REVIEW
        note.with_actor(self.owner).save()
        self.assertEqual(runner.wake_records(), 1)
        run_until(run)
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(run.output, {"id": note.sqid, "title": note.title, "status": "in_review"})
        with system_context(reason="note completed watch assertions"):
            self.assertFalse(StepWatch.objects.filter(step_run=step).exists())
            self.assertEqual(step.attempts.count(), 3)

    def test_trigger_installs_disabled_and_writes_no_events_until_enabled(self) -> None:
        """Demo resources cannot establish an acting identity by themselves."""
        trigger = self.load_trigger()
        self.note()
        with system_context(reason="disabled note trigger assertions"):
            self.assertFalse(trigger.enabled)
            self.assertIsNone(trigger.run_as_id)
            self.assertFalse(TriggerEvent.objects.filter(trigger=trigger).exists())
        Trigger.objects.enable(trigger, actor=self.admin)
        trigger = self.load_trigger()
        self.assertTrue(trigger.enabled)
        self.assertEqual(trigger.run_as_id, self.admin.pk)

    def test_entering_review_admits_the_shipped_workflow_as_the_enabling_actor(self) -> None:
        """The note signal, retained ledger and publication use their actual owners."""
        trigger = Trigger.objects.enable(self.load_trigger(), actor=self.admin)
        note = self.note(status=Note.Status.DRAFT, reviewer=None)
        self.assertEqual(Trigger.objects.drain(), 0)
        with system_context(reason="rejected note trigger assertion"):
            event = TriggerEvent.objects.get(trigger=trigger)
            self.assertTrue(event.rejection)
            self.assertIsNone(event.admitted_at)
        note.status = Note.Status.IN_REVIEW
        note.with_actor(self.owner).save()
        self.assertEqual(Trigger.objects.drain(), 1)
        with system_context(reason="admitted note trigger assertions"):
            event.refresh_from_db()
            run = event.started_run
            self.assertEqual(run.run_as_id, self.admin.pk)
            self.assertEqual(run.origin, "trigger")
            self.assertEqual(run.trigger_event_id, event.pk)
            self.assertEqual(run.record_ref.public_id, note.sqid)
            self.assertIsNotNone(event.admitted_at)
            self.assertEqual(event.rejection, "")
        run_until(run)
        with system_context(reason="triggered note publication assertions"):
            note.refresh_from_db()
            self.assertEqual(run.status, RunStatus.SUCCEEDED)
            self.assertEqual(note.status, Note.Status.ACTIVE)
            self.assertEqual(note.updated_by_id, self.admin.pk)
        note.status = Note.Status.IN_REVIEW
        note.with_actor(self.owner).save()
        self.assertEqual(Trigger.objects.drain(), 0)
        with system_context(reason="note trigger admission identity assertion"):
            self.assertEqual(WorkflowRun.objects.filter(trigger_event=event).count(), 1)

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

    def start_parent(self, note):
        """Load the shipped parent and reach a real wait on its owned child."""

        workflow = load_workflow("example.notes.note_publication_parent", actor=self.admin, allow_non_dev=True)
        workflow.with_actor(self.admin).grant_record_access("starter", self.owner)
        parent = start_run(workflow, actor=self.owner, subject=note)
        run_until(parent)
        with system_context(reason="note parent child assertion"):
            child = WorkflowRun.objects.get(parent_step__run=parent)
            await_step = StepRun.objects.get(run=parent, node_key="await")
            self.assertEqual(await_step.status, StepRunStatus.WAITING)
            self.assertEqual(await_step.waiting_kind, WaitingKind.RUN)
            self.assertEqual(await_step.input, {"run_id": child.sqid})
        return parent, child

    def test_parent_awaits_owned_publication_and_preserves_its_result(self) -> None:
        """The example gives each note a linked child under the same execution actor."""

        note = self.note()
        parent, child = self.start_parent(note)
        with system_context(reason="note child identity assertions"):
            self.assertEqual(child.parent_step.node_key, "start")
            self.assertEqual(child.parent_step.run_id, parent.pk)
            self.assertEqual(child.relation, "owned")
            self.assertEqual(child.origin, "workflow")
            self.assertEqual(child.run_as_id, self.owner.pk)
            self.assertEqual(child.record_ref.public_id, note.sqid)
            self.assertEqual(StepRun.objects.get(run=parent, node_key="start").output, {"run_id": child.sqid})
        self.answer_review(child)
        run_until(child)
        runner.tick()
        run_until(parent)
        with system_context(reason="note parent publication result assertions"):
            note.refresh_from_db()
            await_step = StepRun.objects.get(run=parent, node_key="await")
            self.assertEqual(child.status, RunStatus.SUCCEEDED)
            self.assertEqual(await_step.outcome, "published")
            self.assertEqual(parent.status, RunStatus.SUCCEEDED)
            self.assertEqual(parent.output, child.output)
            self.assertEqual(parent.output["id"], note.sqid)
            self.assertEqual(note.status, Note.Status.ACTIVE)
            self.assertEqual(note.updated_by_id, self.owner.pk)

    def test_parent_retains_a_rejected_child_result_without_publishing(self) -> None:
        """The awaited rejection stays visible on the child and the parent's await row."""

        note = self.note()
        parent, child = self.start_parent(note)
        self.answer_review(child, action="reject", values={"reason": "Needs another revision"})
        run_until(child)
        runner.tick()
        run_until(parent)
        with system_context(reason="note parent rejection assertions"):
            note.refresh_from_db()
            self.assertEqual(child.outcome, "rejected")
            self.assertEqual(StepRun.objects.get(run=parent, node_key="await").outcome, "rejected")
            self.assertEqual(parent.status, RunStatus.SUCCEEDED)
            self.assertEqual(parent.output, child.output)
            self.assertEqual(note.status, Note.Status.IN_REVIEW)

    def test_canceling_parent_cancels_owned_publication_and_open_review(self) -> None:
        """Parent cancellation reaches the live child and its decision lifecycle owner."""

        note = self.note()
        parent, child = self.start_parent(note)
        run_until(child)
        with system_context(reason="note child open review assertion"):
            review = StepRun.objects.get(run=child, node_key="review")
            decision = Decision.objects.get(group=review.decision_group)
            self.assertTrue(decision.is_open)
        cancellation = WorkflowRun.objects.cancel(parent, actor=self.owner)
        with system_context(reason="note parent cancellation assertions"):
            parent.refresh_from_db()
            child.refresh_from_db()
            decision.refresh_from_db()
            note.refresh_from_db()
            self.assertEqual(parent.status, RunStatus.CANCELED)
            self.assertEqual(child.status, RunStatus.CANCELED)
            self.assertFalse(decision.is_open)
            self.assertIn("child", cancellation.message)
            self.assertEqual(note.status, Note.Status.IN_REVIEW)

    def test_parent_routes_a_canceled_child_with_empty_output(self) -> None:
        """Canceling just the child settles the parent through its declared canceled edge."""

        parent, child = self.start_parent(self.note())
        WorkflowRun.objects.cancel(child, actor=self.owner)
        runner.tick()
        run_until(parent)
        with system_context(reason="note parent canceled-child assertions"):
            self.assertEqual(parent.status, RunStatus.SUCCEEDED)
            self.assertEqual(parent.output, {})
            self.assertEqual(StepRun.objects.get(run=parent, node_key="await").outcome, "canceled")

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
        self.assertGreaterEqual(len(self.sent), 4)

    def test_shipped_map_reviews_each_note_and_collects_independent_answers(self) -> None:
        """Two mapped bodies retain distinct seats and a typed successor counts both outcomes."""
        workflow = load_workflow("example.notes.note_review_batch", actor=self.admin, allow_non_dev=True)
        workflow.with_actor(self.admin).grant_record_access("starter", self.owner)
        notes = [self.note(title="First note"), self.note(title="Second note")]
        run = start_run(workflow, actor=self.owner, input={"items": [note.publication_summary() for note in notes]})
        run_until(run)
        with system_context(reason="mapped note review waiting assertions"):
            parent = StepRun.objects.get(run=run, node_key="reviews")
            bodies = list(parent.map_rows().order_by("map_index"))
            decisions = [Decision.objects.get(group=body.decision_group) for body in bodies]
            self.assertEqual((parent.map_total, parent.map_settled), (2, 0))
            self.assertEqual([body.map_index for body in bodies], [0, 1])
            self.assertTrue(all(body.status == StepRunStatus.WAITING for body in bodies))
            self.assertTrue(all(body.waiting_kind == WaitingKind.DECISION for body in bodies))
            self.assertNotEqual(decisions[0].group_id, decisions[1].group_id)
            self.assertEqual([decision.record_public_id for decision in decisions], [note.sqid for note in notes])
            self.assertEqual([decision.basis["title"] for decision in decisions], [note.title for note in notes])
        Decision.objects.decide(decisions[0].pk, actor=self.reviewer, revision=decisions[0].revision,
                                action="approve", values={})
        run_until(run)
        with system_context(reason="mapped note review partial settlement assertions"):
            parent.refresh_from_db()
            decisions[1].refresh_from_db()
            self.assertEqual((parent.map_total, parent.map_settled), (2, 1))
            self.assertTrue(decisions[1].is_open)
            self.assertFalse(StepRun.objects.filter(run=run, node_key="summarize").exists())
        Decision.objects.decide(decisions[1].pk, actor=self.reviewer, revision=decisions[1].revision,
                                action="reject", values={"reason": "Needs revision"})
        run_until(run)
        self.assertEqual(run.status, RunStatus.SUCCEEDED)
        self.assertEqual(run.output, {"approved": 1, "rejected": 1})
        with system_context(reason="mapped note review result assertions"):
            parent.refresh_from_db()
            self.assertEqual([item["output"]["id"] for item in parent.output], [note.sqid for note in notes])
            self.assertEqual((parent.map_total, parent.map_settled), (2, 2))
            self.assertEqual(StepRun.objects.get(run=run, node_key="summarize").input, parent.output)

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
        self.assertFalse(runner.execute(step_run.pk))
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
            self.assertEqual(workflow.subject_model, Note._meta.label)
            self.assertEqual(workflow.published.definition.step("validate").subject, Note._meta.label)

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
            self.assertEqual(installed.subject_model, Note._meta.label)
            self.assertEqual(set(installed.published.definition.nodes), {"validate", "review", "publish"})

    def test_steps_declare_their_typed_contract(self) -> None:
        """The registry classes own schemas, subjects and named outcomes."""

        for step in (ValidateNotePublication, ReviewNotePublication, PublishNote):
            self.assertEqual(step.output_schema(), NotePublicationOutput.model_json_schema())
        for step in (ValidateNotePublication, PublishNote):
            self.assertEqual(step.subject, "notes.Note")
        self.assertIsNone(ReviewNotePublication.subject)
        self.assertEqual(ValidateNotePublication.available_outcomes(None), {
            "needs_review": "Needs review", "ok": "Ready", "error": "Error",
        })
        self.assertEqual(set(ReviewNotePublication.available_outcomes(None)), {
            "approved", "rejected", "expired", "superseded", "error",
        })
        self.assertEqual(PublishNote.available_outcomes(None), {"published": "Published", "error": "Error"})
