"""Question lifecycle on real emitted models, with and without optional Work."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist
from django.core.management import call_command
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import run_composed_tests


class ClarificationCase(TransactionTestCase):
    """Shared native proposal fixture; no hand-built donor or permission graph."""

    __test__ = False
    storage = "registry"

    def setUp(self):
        settings = override_settings(
            REBAC_LOCAL_BACKEND_STORAGE=self.storage,
            REBAC_STRICT_MODE=True,
            REBAC_SUPERUSER_BYPASS=False,
        )
        settings.enable()
        self.addCleanup(settings.disable)
        call_command("rebac", "sync", verbosity=0)
        for app, names in (
            ("proposals", ("Round", "Proposal")),
            ("projects", ("Project", "Task", "Milestone")),
        ):
            for name in names:
                setattr(self, name, apps.get_model(app, name))
        for name in ("manager", "asker", "recipient", "member", "outsider"):
            setattr(
                self,
                name,
                get_user_model().objects.create_person_as_system(
                    username=name, email=f"{name}@example.test", reason="tests.proposals_clarifications.person"
                ),
            )
        with system_context(reason="tests.proposals_clarifications.setup"):
            self.project = self.Project.objects.create(title="Review target", owner=self.manager)
            boundary = self.Milestone.objects.create(project=self.project, name="Questions")
            self.round = self.Round.objects.create(
                project=self.project,
                facilitator=self.manager,
                name="Review",
                last_call_at=timezone.now() + timedelta(days=1),
                submission_deadline=timezone.now() + timedelta(days=2),
                clarifications_shared_until=boundary,
            )
        with actor_context(self.manager):
            self.as_user(self.round, self.manager).admit(self.asker)
            self.as_user(self.round, self.manager).admit(self.recipient)

    def as_user(self, row, user):
        return type(row)._base_manager.get(pk=row.pk).with_actor(user)

    def ask(self, **kwargs):
        with actor_context(self.asker):
            return self.as_user(self.round, self.asker).ask("Question", "Question details", **kwargs)

    def execute(self, document, variables, user):
        request = RequestFactory().post("/graphql/")
        request.user = user
        with actor_context(user):
            return GraphQLSchemas.from_discovery().build("console").execute_sync(
                document, variable_values=variables, context_value=SimpleNamespace(request=request)
            )

    def task_action(self, verb, task, user):
        result = self.execute(
            f"mutation TaskAction($id: ID!) {{ {verb}(id: $id) {{ ok }} }}",
            {"id": str(task.sqid)},
            user,
        )
        self.assertIsNone(result.errors, result.errors)
        self.assertTrue(result.data[verb]["ok"], result.data)
        return self.Task._base_manager.get(pk=task.pk)

    def assert_queue_free(self, task):
        if "work" in apps.app_configs:
            self.assertIsNone(task.queue_id)
            self.assertIsNone(task.stage_id)
            self.assertIsNone(apps.get_model("work", "Queue").objects.personal_for(self.asker))
        else:
            with self.assertRaises(FieldDoesNotExist):
                task._meta.get_field("queue")
        self.assertIsNone(task.created_by_id)
        self.assertIsNone(task.owner_id)

    def test_queue_free_lifecycle(self):
        task = self.ask(client_creation_key="question")
        self.assertEqual(self.ask(client_creation_key="question").pk, task.pk)
        self.assert_queue_free(task)
        with actor_context(self.manager):
            passed = self.as_user(self.round, self.manager).pass_clarification(task, self.recipient)
        self.assertEqual(passed.assignee_id, self.recipient.pk)
        self.assertIsNotNone(passed.clarification_passed_at)
        self.assert_queue_free(passed)
        self.assert_recipient_discusses_only(passed)
        completed = self.task_action("complete_task", passed, self.manager)
        self.assertEqual(completed.status, "done")
        reopened = self.task_action("reopen_task", completed, self.manager)
        self.assertEqual(reopened.status, "open")
        self.assert_queue_free(reopened)

    def test_queue_free_manager_question(self):
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).ask("Manager question", "Details", recipient=self.recipient)
        self.assertIsNotNone(task.clarification_passed_at)
        self.assertEqual(task.assignee_id, self.recipient.pk)
        self.assert_queue_free(task)

    def assert_recipient_discusses_only(self, task):
        recipient_task = self.as_user(task, self.recipient)
        for permission in ("read", "comment"):
            self.assertTrue(recipient_task.has_access(permission), permission)
        for permission in ("write", "narrow", "widen", "share"):
            self.assertFalse(recipient_task.has_access(permission), permission)
        for permission in ("read", "comment", "write", "narrow", "widen", "share"):
            self.assertTrue(self.as_user(task, self.manager).has_access(permission), permission)

    def test_manager_question_recipient_cannot_write_or_narrow(self):
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).ask("Manager question", "Details", recipient=self.recipient)
        self.assert_recipient_discusses_only(task)

    def test_published_question_recipient_cannot_write_or_narrow(self):
        task = self.ask()
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).pass_clarification(task, self.recipient)
            self.as_user(task, self.manager).set_visibility("inherited")
        self.assert_recipient_discusses_only(task)

    def test_widened_question_read_does_not_grant_replies(self):
        with system_context(reason="tests.proposals_clarifications.named_asker"):
            round = self.Round.objects.get(pk=self.round.pk)
            round.clarification_askers = "named"
            round.save(update_fields=("clarification_askers",))
        with actor_context(self.manager):
            self.as_user(self.round, self.manager).admit(self.member)
        task = self.ask()
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).pass_clarification(
                task, self.recipient, audience="asker"
            )
            self.as_user(task, self.manager).set_visibility("inherited")

        widened_reader = self.as_user(task, self.member)
        self.assertTrue(widened_reader.has_access("read"))
        self.assertFalse(widened_reader.has_access("comment"))
        with actor_context(self.member), self.assertRaises(PermissionDenied):
            widened_reader.message_post("Cannot give the first answer")

        for author, body in (
            (self.recipient, "First answer"),
            (self.asker, "Asker follow-up"),
            (self.manager, "Manager follow-up"),
        ):
            held = self.as_user(task, author)
            self.assertTrue(held.has_access("comment"))
            with actor_context(author):
                self.assertIsNotNone(held.message_post(body).pk)

        with system_context(reason="tests.proposals_clarifications.requester"):
            round = self.Round.objects.get(pk=self.round.pk)
            round.requester_party = apps.get_model("parties", "Person").objects.for_user(self.outsider)
            round.save(update_fields=("requester_party",))
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).pass_clarification(
                task, self.outsider, audience="asker"
            )
        self.assertTrue(self.as_user(task, self.recipient).has_access("read"))
        self.assertFalse(self.as_user(task, self.recipient).has_access("comment"))
        with actor_context(self.outsider):
            self.assertIsNotNone(self.as_user(task, self.outsider).message_post("Requester reply").pk)
        with actor_context(self.member), self.assertRaises(PermissionDenied):
            self.as_user(task, self.member).message_post("Cannot reply later")

    def test_asker_with_explicit_read_can_reply_after_surrender(self):
        task = self.ask(audience="managers")
        self.assertFalse(self.as_user(task, self.asker).has_access("read"))
        with system_context(reason="tests.proposals_clarifications.asker_share"):
            write_relationships([
                RelationshipTuple(to_object_ref(task), "reader", to_subject_ref(self.asker)),
            ])
        held = self.as_user(task, self.asker)
        self.assertTrue(held.has_access("read"))
        self.assertTrue(held.has_access("comment"))
        with actor_context(self.asker):
            self.assertIsNotNone(held.message_post("Further detail").pk)

    def test_task_audience_traverses_reverse_source_proposal(self):
        with system_context(reason="tests.proposals_clarifications.track_task"):
            track = self.Project.objects.create(title="Response track", owner=None)
            proposal = self.Proposal.objects.get(round=self.round, responder=self.recipient)
            self.Proposal.objects.filter(pk=proposal.pk).update(track=track)
            task = self.Task.objects.create(project=track, title="Track work")
        question = self.ask()
        document = """query Audience($id: String!) {
          project_tasks_by_pk(id: $id) {
            id permissions shared_with_responders project { source_proposal { id } }
          }
        }"""
        for actor, row, expected in (
            (self.manager, task, {"id": str(proposal.sqid)}),
            (self.recipient, task, {"id": str(proposal.sqid)}),
            (self.asker, question, None),
        ):
            result = self.execute(document, {"id": str(row.sqid)}, actor)
            self.assertIsNone(result.errors, result.errors)
            self.assertEqual(result.data["project_tasks_by_pk"]["project"]["source_proposal"], expected)


class ClarificationProfileTests(ClarificationCase):
    def test_bridge_is_optional(self):
        with self.assertRaises(FieldDoesNotExist):
            self.Round._meta.get_field("clarification_queue")
        self.assertNotIn("proposals_work", apps.app_configs)
        # Both profiles must build their complete public and console schemas.
        schemas = GraphQLSchemas.from_discovery().render_sdl()
        self.assertTrue(schemas)
        self.assertTrue(all("clarification_queue" not in sdl for sdl in schemas.values()))


class ProposalsOnlyTests(ClarificationProfileTests):
    def test_work_is_absent(self):
        self.assertNotIn("work", apps.app_configs)


class WithWorkTests(ClarificationProfileTests):
    def test_work_is_present(self):
        self.assertIn("work", apps.app_configs)


class DenormalizedProposalsOnlyTests(ProposalsOnlyTests):
    storage = "denormalized"


class DenormalizedWithWorkTests(WithWorkTests):
    storage = "denormalized"


@pytest.mark.parametrize("with_work", (False, True), ids=("proposals-only", "work-without-bridge"))
@pytest.mark.parametrize("storage", ("registry", "denormalized"))
def test_proposals_clarification_profiles(tmp_path: Path, with_work: bool, storage: str):
    roots = ("angee.proposals", "angee.work") if with_work else ("angee.proposals",)
    test_class = ("Denormalized" if storage == "denormalized" else "") + (
        "WithWorkTests" if with_work else "ProposalsOnlyTests"
    )
    run_composed_tests(tmp_path, f"tests.test_proposals_clarifications.{test_class}", app=roots)
