"""The optional proposals/work bridge on the native composed model graph."""

import tomllib
from datetime import timedelta
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)

from tests.composed_host import run_composed_tests
from tests.test_proposals_clarifications import ClarificationCase


class ProposalsWorkTests(ClarificationCase):
    def setUp(self):
        super().setUp()
        self.Queue = apps.get_model("work", "Queue")
        self.Stage = apps.get_model("work", "Stage")
        with system_context(reason="tests.proposals_work.queue"):
            # Generated relation inputs resolve through the actor's readable directory.
            write_relationships([
                RelationshipTuple(ObjectRef("iam/directory", "main"), "reader", to_subject_ref(self.manager)),
            ])
            self.queue = self.Queue.objects.create(
                name="Questions", key="QUEST", owner=self.manager, provision_stages=False
            )
            self.ready = self.Stage.objects.create(queue=self.queue, name="Ready", category="unstarted", position=0)
            self.started = self.Stage.objects.create(queue=self.queue, name="Working", category="started", position=1)
            self.Stage.objects.create(queue=self.queue, name="Later", category="started", position=2)
            self.completed = self.Stage.objects.create(queue=self.queue, name="Done", category="completed", position=3)
            self.Stage.objects.create(queue=self.queue, name="Later done", category="completed", position=4)
            self.queue.default_stage = self.ready
            self.queue.save(update_fields=("default_stage", "updated_at"))
            apps.get_model("spaces", "Membership").objects.create(
                group=self.queue.group_ptr,
                party=apps.get_model("parties", "Person")._base_manager.get(user=self.member),
                role="member",
                is_confirmed=True,
            )

    def route(self, queue=None):
        with actor_context(self.manager):
            round = self.as_user(self.round, self.manager)
            round.clarification_queue = queue or self.queue
            round.full_clean()
            round.save(update_fields=("clarification_queue", "updated_at"))

    def pass_question(self, task):
        with actor_context(self.manager):
            return self.as_user(self.round, self.manager).pass_clarification(task, self.recipient)

    def update_queue(self, queue, user):
        return self.execute(
            """mutation Queue($id: String!, $queue: ID) {
                update_proposal_rounds_by_pk(pk_columns: {id: $id}, _set: {clarification_queue: $queue}) {
                    id clarification_queue { id }
                }
            }""",
            {"id": str(self.round.sqid), "queue": str(queue.sqid) if queue else None},
            user,
        )

    def test_composed_column_contract(self):
        field = self.Round._meta.get_field("clarification_queue")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.remote_field.related_name, "+")
        self.assertEqual(field.related_model, self.Queue)
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")
        self.assertEqual(list(apps.get_app_config("proposals_work").get_models()), [])

    def test_generated_round_queue_writes_and_filter(self):
        result = self.update_queue(self.queue, self.manager)
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(
            result.data["update_proposal_rounds_by_pk"]["clarification_queue"]["id"], str(self.queue.sqid)
        )
        result = self.execute(
            """query QueuedRounds($queue: String!) {
                proposal_rounds(where: {clarification_queue: {_eq: $queue}}) { id }
            }""",
            {"queue": str(self.queue.sqid)},
            self.manager,
        )
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["proposal_rounds"], [{"id": str(self.round.sqid)}])
        redacted = self.execute(
            """query Round($id: String!) {
                proposal_rounds_by_pk(id: $id) { id clarification_queue { id } }
            }""",
            {"id": str(self.round.sqid)},
            self.asker,
        )
        self.assertIsNone(redacted.errors, redacted.errors)
        self.assertIsNone(redacted.data["proposal_rounds_by_pk"]["clarification_queue"])
        refused = self.update_queue(None, self.asker)
        self.assertTrue(refused.errors or refused.data["update_proposal_rounds_by_pk"] is None)
        self.assertEqual(self.Round._base_manager.get(pk=self.round.pk).clarification_queue_id, self.queue.pk)
        with actor_context(self.asker), self.assertRaises(PermissionDenied):
            round = self.as_user(self.round, self.asker)
            round.clarification_queue = None
            round.save(update_fields=("clarification_queue",))
        cleared = self.update_queue(None, self.manager)
        self.assertIsNone(cleared.errors, cleared.errors)
        self.assertIsNone(self.Round._base_manager.get(pk=self.round.pk).clarification_queue_id)

    def test_generated_insert_accepts_queue(self):
        result = self.execute(
            """mutation Round($project: ID!, $facilitator: ID!, $queue: ID!, $last: DateTime!, $deadline: DateTime!) {
                insert_proposal_rounds_one(object: {
                    name: "Another review", project: $project, facilitator: $facilitator,
                    clarification_queue: $queue, last_call_at: $last, submission_deadline: $deadline
                }) { id clarification_queue { id } }
            }""",
            {
                "project": str(self.project.sqid),
                "facilitator": str(self.manager.sqid),
                "queue": str(self.queue.sqid),
                "last": (timezone.now() + timedelta(days=1)).isoformat(),
                "deadline": (timezone.now() + timedelta(days=2)).isoformat(),
            },
            self.manager,
        )
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data["insert_proposal_rounds_one"]["clarification_queue"]["id"], str(self.queue.sqid))

    def test_personal_queue_refused_and_routing_queue_protected(self):
        personal = self.Queue.objects.provision_personal(self.manager)
        candidate = self.as_user(self.round, self.manager)
        candidate.clarification_queue = personal
        with self.assertRaisesMessage(ValidationError, "non-personal queue"):
            candidate.clean()
        refused = self.update_queue(personal, self.manager)
        self.assertTrue(refused.errors)
        self.assertIn("non-personal queue", refused.errors[0].message)
        self.route()
        with system_context(reason="tests.proposals_work.protect"):
            for model in (self.Queue, apps.get_model("spaces", "Group")):
                with self.subTest(model=model), self.assertRaises(ProtectedError):
                    model._base_manager.filter(pk=self.queue.pk).delete()

    def test_native_stage_lifecycle_and_manager_ask(self):
        """D25 gives the manager stage exits; D35 keeps recipients discussion-only."""

        self.route()
        task = self.ask()
        self.assertEqual((task.queue_id, task.stage_id), (self.queue.pk, self.ready.pk))
        passed = self.pass_question(task)
        self.assertEqual(passed.stage_id, self.started.pk)
        completed = self.task_action("complete_task", passed, self.manager)
        self.assertEqual((completed.status, completed.stage_id), ("done", self.completed.pk))
        reopened = self.task_action("reopen_task", completed, self.manager)
        self.assertEqual((reopened.status, reopened.stage_id), ("open", self.ready.pk))
        with actor_context(self.manager):
            manager_question = self.as_user(self.round, self.manager).ask(
                "Manager question", "Details", recipient=self.recipient
            )
        self.assertEqual(manager_question.stage_id, self.started.pk)
        self.assertIsNotNone(manager_question.clarification_passed_at)

    def test_queue_changes_only_route_new_questions(self):
        self.route()
        task = self.ask()
        cleared = self.update_queue(None, self.manager)
        self.assertIsNone(cleared.errors, cleared.errors)
        self.assertEqual(self.Task._base_manager.get(pk=task.pk).queue_id, self.queue.pk)
        self.assert_queue_free(self.ask())

    def test_missing_started_stage_rolls_back_pass_and_manager_ask(self):
        self.route()
        with system_context(reason="tests.proposals_work.no_started"):
            self.Stage._base_manager.filter(queue=self.queue, category="started").delete()
        task = self.ask()
        before = (task.assignee_id, task.stage_id, task.clarification_passed_at, task.revision)
        with self.assertRaisesMessage(ValidationError, "Queue has no started stage"):
            self.pass_question(task)
        stored = self.Task._base_manager.get(pk=task.pk)
        self.assertEqual((stored.assignee_id, stored.stage_id, stored.clarification_passed_at, stored.revision), before)
        count = self.Task._base_manager.count()
        with actor_context(self.manager), self.assertRaisesMessage(ValidationError, "Queue has no started stage"):
            self.as_user(self.round, self.manager).ask("Manager question", "Details", recipient=self.recipient)
        self.assertEqual(self.Task._base_manager.count(), count)

    def test_queue_roster_reads_and_comments_only_on_inherited_questions(self):
        self.route()
        inherited = self.ask()
        restricted = self.ask(audience="managers")
        for task, allowed in ((inherited, True), (restricted, False)):
            with self.subTest(visibility=task.visibility), actor_context(self.member):
                held = self.as_user(task, self.member)
                self.assertEqual(held.has_access("read"), allowed)
                self.assertEqual(held.has_access("comment"), allowed)
                self.assertEqual(self.Task.objects.as_user(self.member).filter(pk=task.pk).exists(), allowed)
                if allowed:
                    message = held.message_post(body="Queue member reply")
                    self.assertIsNotNone(message.pk)
                else:
                    with self.assertRaises(PermissionDenied):
                        held.message_post(body="Must not be posted")
        self.assertFalse(self.as_user(inherited, self.outsider).has_access("read"))

    def private_track(self):
        with system_context(reason="tests.proposals_work.storage"):
            backend = apps.get_model("storage", "Backend").objects.create(slug="local", backend_class="local")
            apps.get_model("storage", "Drive").objects.create(
                slug="default", name="Default", prefix="default", backend=backend
            )
        self.enterContext(self.settings(ANGEE_STORAGE_DEFAULT_DRIVE="default"))
        with actor_context(self.asker):
            proposal = self.Proposal._base_manager.get(round=self.round, responder=self.asker).with_actor(self.asker)
            return proposal, proposal.create_track()

    def test_unpublished_track_rejects_shared_queue_by_every_placement(self):
        _proposal, track = self.private_track()
        with system_context(reason="tests.proposals_work.track_guard"):
            cycle = apps.get_model("work", "Cycle").objects.create(
                queue=self.queue,
                number=1,
                starts_on=timezone.localdate(),
                ends_on=timezone.localdate() + timedelta(days=7),
            )
            for fields in ({"queue": self.queue}, {"stage": self.ready}, {"cycle": cycle}):
                with self.subTest(fields=fields), self.assertRaisesMessage(ValidationError, "personal queue"):
                    self.Task.objects.create(project=track, title="Private work", **fields)
            self.assertFalse(self.Task._base_manager.filter(project=track).exists())
            for fields in ({"queue": self.queue}, {"stage": self.ready}, {"cycle": cycle}):
                task = self.Task.objects.create(project=track, title="Unqueued private work")
                for name, value in fields.items():
                    setattr(task, name, value)
                with self.subTest(fields=fields), self.assertRaisesMessage(ValidationError, "personal queue"):
                    task.save(update_fields=tuple(fields))
                stored = self.Task._base_manager.get(pk=task.pk)
                self.assertIsNone(stored.queue_id)
                self.assertIsNone(stored.stage_id)
                self.assertIsNone(stored.cycle_id)
            outside = self.Task.objects.create(project=self.project, title="Ordinary work", queue=self.queue)
            outside.project = track
            with self.assertRaisesMessage(ValidationError, "personal queue"):
                outside.save(update_fields=("project",))
            self.assertEqual(self.Task._base_manager.get(pk=outside.pk).project_id, self.project.pk)

    def test_personal_queue_holders_and_publication(self):
        proposal, track = self.private_track()
        # A round team moderator is a manager through the live graph, not a copied list.
        with system_context(reason="tests.proposals_work.team"):
            team = apps.get_model("spaces", "Group").objects.create(name="Review team", owner=self.manager)
            apps.get_model("spaces", "Membership").objects.create(
                group=team,
                party=apps.get_model("parties", "Person")._base_manager.get(user=self.member),
                role="moderator",
                is_confirmed=True,
            )
            round = self.Round._base_manager.get(pk=self.round.pk)
            round.team = team
            round.save(update_fields=("team",))
        for holder in (self.asker, self.manager, self.member):
            queue = self.Queue.objects.provision_personal(holder)
            with self.subTest(holder=holder), system_context(reason="tests.proposals_work.personal"):
                task = self.Task.objects.create(project=track, title="Private work", queue=queue)
                self.assertEqual(task.queue_id, queue.pk)
        outsider_queue = self.Queue.objects.provision_personal(self.outsider)
        with system_context(reason="tests.proposals_work.outsider"), self.assertRaisesMessage(
            ValidationError, "personal queue"
        ):
            self.Task.objects.create(project=track, title="Must not escape", queue=outsider_queue)
        with actor_context(self.outsider), system_context(reason="tests.proposals_work.implicit_outsider"):
            with self.assertRaisesMessage(ValidationError, "personal queue"):
                self.Task.objects.create(project=track, title="Implicit personal queue must not escape")
        with actor_context(self.asker), system_context(reason="tests.proposals_work.implicit_responder"):
            task = self.Task.objects.create(project=track, title="Implicit responder queue")
            self.assertEqual(task.queue_id, self.Queue.objects.personal_for(self.asker).pk)
        with actor_context(self.manager):
            self.as_user(proposal, self.manager).publish_track()
        with system_context(reason="tests.proposals_work.published"):
            task = self.Task.objects.create(project=track, title="Published work", queue=self.queue)
            self.assertEqual(task.queue_id, self.queue.pk)

    def test_track_guard_preserves_partial_and_deferred_saves(self):
        _proposal, track = self.private_track()
        queue = self.Queue.objects.provision_personal(self.asker)
        with system_context(reason="tests.proposals_work.partial"):
            task = self.Task.objects.create(project=track, title="Private work", queue=queue)
            task.project = self.project  # Not included in this write.
            task.title = "Renamed"
            task.save(update_fields=("title",))
            stored = self.Task._base_manager.get(pk=task.pk)
            self.assertEqual(stored.queue_id, queue.pk)
            self.assertEqual(stored.project_id, track.pk)
            deferred = self.Task._base_manager.defer("project", "created_at").get(pk=task.pk)
            self.assertTrue({"project_id", "created_at"} <= deferred.get_deferred_fields())
            deferred.title = "Deferred rename"
            deferred.save()
            # Save hooks may load deferred dependencies; they must preserve their values.
            saved = self.Task._base_manager.get(pk=task.pk)
            self.assertEqual((saved.project_id, saved.queue_id), (track.pk, queue.pk))
            self.assertEqual(saved.created_at, stored.created_at)
            self.assertEqual(saved.title, "Deferred rename")


class DenormalizedProposalsWorkTests(ProposalsWorkTests):
    storage = "denormalized"


def test_proposals_work_manifest():
    addon = Path(__file__).resolve().parents[1] / "addons/angee/proposals_work"
    manifest = tomllib.loads((addon / "addon.toml").read_text())
    assert manifest["addon"]["depends_on"] == ["angee.proposals", "angee.work"]
    assert "resources" not in manifest
    assert not tuple(addon.glob("permissions*.zed"))
    proposals = tomllib.loads((addon.parent / "proposals/addon.toml").read_text())
    assert "angee.work" not in proposals["addon"]["depends_on"]
    assert "work." not in (addon.parent / "proposals/models.py").read_text()


@pytest.mark.parametrize("test_class", ("ProposalsWorkTests", "DenormalizedProposalsWorkTests"))
def test_proposals_work_composition(tmp_path: Path, test_class: str):
    run_composed_tests(tmp_path, f"tests.test_proposals_work.{test_class}", app="angee.proposals_work")
