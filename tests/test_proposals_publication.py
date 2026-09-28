"""D26 contracts on the emitted proposals + projects + messaging model graph."""

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, system_context, to_subject_ref

from angee.graphql.schema import GraphQLSchemas
from angee.proposals.models import ClarificationWidenBlocked, PublishedQuestion
from tests.composed_host import run_composed_tests


@pytest.mark.parametrize("case", ("RegistryPublicationTests", "DenormalizedPublicationTests"))
def test_publication_contracts(tmp_path: Path, case: str) -> None:
    """Use the existing isolated host so schema tests bind real composed models."""
    run_composed_tests(tmp_path, f"tests.test_proposals_publication.{case}", app="angee.proposals")


class PublicationCases(TransactionTestCase):
    """Shared cases; invoked only by the isolated composed host."""

    __test__ = False
    storage = "registry"

    def setUp(self) -> None:
        settings = override_settings(REBAC_LOCAL_BACKEND_STORAGE=self.storage, REBAC_SUPERUSER_BYPASS=False)
        settings.enable()
        self.addCleanup(settings.disable)
        call_command("rebac", "sync", verbosity=0)
        self.Task = apps.get_model("projects", "Task")
        self.Round = apps.get_model("proposals", "Round")
        self.Message = apps.get_model("messaging", "Message")
        users = get_user_model().objects
        self.manager, self.asker, self.recipient, self.reader = (
            users.create_person_as_system(
                username=name, email=f"{name}@example.test", reason="tests.proposals.publication.person",
            )
            for name in ("manager", "asker", "recipient", "reader")
        )
        with system_context(reason="tests.proposals.publication.setup"):
            self.project = apps.get_model("projects", "Project").objects.create(
                title="Question audience", owner=self.manager,
            )
            boundary = apps.get_model("projects", "Milestone").objects.create(
                project=self.project, name="Shared questions", sort_order=1024,
            )
            self.round = self.Round.objects.create(
                project=self.project,
                facilitator=self.manager,
                name="Publication round",
                clarifications_shared_until=boundary,
                last_call_at=timezone.now() + timedelta(days=1),
                submission_deadline=timezone.now() + timedelta(days=2),
            )
        with actor_context(self.manager):
            round = self.as_user(self.round, self.manager)
            round.admit(self.asker)
            round.admit(self.recipient)

    def as_user(self, row: Any, user: Any) -> Any:
        """Reload without a factory's system binding before a user operation."""
        return type(row)._base_manager.get(pk=row.pk).with_actor(user)

    def question(self, *, restricted: bool = True, manager: bool = False, responders: bool = False) -> Any:
        """Ask through the native verb and optionally narrow the unpassed question."""
        actor = self.manager if manager else self.asker
        with actor_context(actor):
            question = self.as_user(self.round, actor).ask(
                "Question", "Anonymous question body",
                recipient=("responders" if responders else self.recipient) if manager else None,
            )
            question = self.as_user(question, actor)
        if restricted and question.visibility != "restricted":
            # Visibility is tracked in chatter. Let the manager narrow so an
            # otherwise empty question has no automatic message by its asker.
            with actor_context(self.manager):
                question = self.as_user(question, self.manager)
                question.set_visibility("restricted")
        return question

    def post(self, question: Any, actor: Any, *, note: bool = False) -> Any:
        """Exercise both public user-authored chatter entrypoints."""
        with actor_context(actor):
            record = self.as_user(question, actor)
            return record.message_log("Note") if note else record.message_post("Answer")

    def execute(self, document: str, variables: dict[str, Any], actor: Any, *, schema_name: str = "console") -> Any:
        """Execute the production schema with its normal request and actor context."""
        request = RequestFactory().post(f"/graphql/{schema_name}/")
        request.user = actor
        with actor_context(actor):
            return GraphQLSchemas.from_discovery().build(schema_name).execute_sync(
                document, variable_values=variables, context_value=SimpleNamespace(request=request),
            )

    def data(self, result: Any) -> dict[str, Any]:
        """Require in-band action results rather than a GraphQL exception."""
        self.assertIsNone(result.errors, result.errors)
        self.assertIsNotNone(result.data)
        return result.data

    def test_hidden_asker_blocks_visibility_and_pass_atomically(self) -> None:
        question = self.question()
        message = self.post(question, self.asker)
        self.assertEqual(message.created_by_id, self.asker.pk)
        with system_context(reason="tests.proposals.publication.validation"):
            locked = self.Task.objects.get(pk=question.pk)
            with CaptureQueriesContext(connection) as queries, self.assertRaises(ClarificationWidenBlocked) as error:
                locked.validate_visibility("inherited")
            self.assertEqual(error.exception.code, "HIDDEN_ASKER_IN_THREAD")
            self.assertEqual(len(queries), 1)
            self.assertEqual(queries[0]["sql"].upper().count("EXISTS("), 1)
        before = self.Task._base_manager.filter(pk=question.pk).values().get()
        with actor_context(self.manager):
            with self.assertRaises(ClarificationWidenBlocked):
                self.as_user(question, self.manager).set_visibility("inherited")
            with self.assertRaises(ClarificationWidenBlocked):
                self.as_user(self.round, self.manager).pass_clarification(question, self.recipient)
        self.assertEqual(self.Task._base_manager.filter(pk=question.pk).values().get(), before)

    def test_named_asker_and_manager_question_can_widen(self) -> None:
        manager_question = self.question(manager=True)
        self.post(manager_question, self.manager)
        with actor_context(self.manager):
            self.as_user(manager_question, self.manager).set_visibility("inherited")
        question = self.question()
        self.post(question, self.asker)
        with system_context(reason="tests.proposals.publication.name_askers"):
            round = self.Round.objects.get(pk=self.round.pk)
            round.clarification_askers = "named"
            round.save(update_fields=("clarification_askers",))
        with actor_context(self.manager):
            self.as_user(question, self.manager).set_visibility("inherited")
        self.post(question, self.asker)

    def test_asker_attributed_tracking_message_also_blocks_widening(self) -> None:
        question = self.question(restricted=False)
        with actor_context(self.asker):
            self.as_user(question, self.asker).set_visibility("restricted")
        with actor_context(self.manager), self.assertRaises(ClarificationWidenBlocked):
            self.as_user(question, self.manager).set_visibility("inherited")

    def test_other_threads_and_other_authors_do_not_block(self) -> None:
        other = self.question()
        self.post(other, self.asker)
        question = self.question()
        self.post(question, self.manager)
        with actor_context(self.manager):
            passed = self.as_user(self.round, self.manager).pass_clarification(question, self.recipient)
        self.assertEqual(passed.visibility, "inherited")
        self.assertIsNotNone(passed.clarification_passed_at)

    def test_published_question_cannot_narrow_but_visibility_replay_is_allowed(self) -> None:
        question = self.question(restricted=False)
        with actor_context(self.manager):
            question = self.as_user(self.round, self.manager).pass_clarification(question, self.recipient)
            question = self.as_user(question, self.manager)
            revision = question.revision
            question.set_visibility("inherited", expected_revision=revision)
            self.assertEqual(question.revision, revision)
            with self.assertRaises(PublishedQuestion) as error:
                question.set_visibility("restricted")
            self.assertIsInstance(error.exception, ValidationError)
            self.assertEqual(error.exception.code, "PUBLISHED_QUESTION")
        question.refresh_from_db()
        self.assertEqual(question.visibility, "inherited")
        self.assertEqual(question.revision, revision)

    def test_unpassed_shared_question_can_be_passed_to_asker(self) -> None:
        question = self.question(restricted=False)
        with actor_context(self.manager):
            passed = self.as_user(self.round, self.manager).pass_clarification(
                question, self.recipient, audience="asker",
            )
        self.assertEqual(passed.visibility, "restricted")
        self.assertIsNotNone(passed.clarification_passed_at)
        self.post(passed, self.asker)

    def test_all_responders_question_stays_shared_while_waiting(self) -> None:
        question = self.question(restricted=False, manager=True, responders=True)
        self.assertIsNotNone(question.clarification_passed_at)
        with actor_context(self.manager):
            question = self.as_user(question, self.manager)
            self.assertEqual(
                {entry["id"] for entry in question.clarification_waiting()}, {self.asker.pk, self.recipient.pk},
            )
            with self.assertRaises(PublishedQuestion):
                question.set_visibility("restricted")
        self.post(question, self.asker)
        self.post(question, self.manager)

    def test_stale_post_cannot_unmask_after_publication(self) -> None:
        for passed in (False, True):
            with self.subTest(passed=passed):
                question = self.question()
                stale = self.as_user(question, self.asker)
                with actor_context(self.manager):
                    if passed:
                        self.as_user(self.round, self.manager).pass_clarification(question, self.recipient)
                    else:
                        self.as_user(question, self.manager).set_visibility("inherited")
                before = self.Message._base_manager.count()
                # The pinned asker controls attribution, even with another ambient
                # actor and an explicit replay-identity override.
                with actor_context(self.manager):
                    for method in (stale.message_post, stale.message_log):
                        with self.assertRaises(ClarificationWidenBlocked) as error:
                            method("Must not be shared", creation_actor=to_subject_ref(self.manager))
                        self.assertEqual(error.exception.code, "HIDDEN_ASKER_IN_THREAD")
                self.assertEqual(self.Message._base_manager.count(), before)
                self.post(question, self.manager)
                self.post(question, self.recipient)

    def test_ordinary_task_keeps_native_visibility_and_posting(self) -> None:
        with actor_context(self.manager):
            task = self.Task.objects.create(title="Ordinary task", project=self.project)
            task.set_visibility("restricted")
            task.message_post("Ordinary comment")
            task.set_visibility("inherited")
            task.set_visibility("restricted")

    def test_blocker_projection_is_permission_gated_and_batched(self) -> None:
        question = self.question()
        self.post(question, self.asker)
        with actor_context(self.manager):
            record = self.as_user(question, self.manager)
            record.grant_record_access("reader", self.reader)
            record.grant_record_access("reader", self.recipient)
        document = "query { tasks { id clarificationWidenBlocker } }"
        for schema_name in ("public", "console"):
            for actor in (self.manager, self.asker, self.recipient, self.reader):
                with self.subTest(schema=schema_name, actor=actor.username):
                    rows = self.data(self.execute(document, {}, actor, schema_name=schema_name))["tasks"]
                    self.assertEqual(rows, [{
                        "id": str(question.sqid),
                        "clarificationWidenBlocker": "hidden_asker_message" if actor == self.manager else None,
                    }])
        # Narrow selection: no dependency columns selected to conceal an N+1.
        self.data(self.execute(document, {}, self.manager))
        with CaptureQueriesContext(connection) as queries:
            self.data(self.execute(document, {}, self.manager))
        baseline = len(queries)
        for _ in range(9):
            self.question()
        with CaptureQueriesContext(connection) as queries:
            rows = self.data(self.execute(document, {}, self.manager))["tasks"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(len(queries), baseline)

    def test_graphql_actions_refuse_in_band_and_leave_question_unchanged(self) -> None:
        question = self.question()
        self.post(question, self.asker)
        before = self.Task._base_manager.filter(pk=question.pk).values().get()
        payload = self.data(self.execute(
            """mutation($id: ID!) {
              setTaskVisibility(id: $id, visibility: INHERITED) { ok validationErrors }
            }""",
            {"id": str(question.sqid)}, self.manager,
        ))["setTaskVisibility"]
        self.assertFalse(payload["ok"])
        self.assertIn("hidden asker", str(payload["validationErrors"]))
        payload = self.data(self.execute(
            """mutation($round: ID!, $task: ID!, $recipient: ID!) {
              passProposalRoundClarification(round: $round, task: $task, recipient: $recipient) {
                ok validationErrors
              }
            }""",
            {"round": str(self.round.sqid), "task": str(question.sqid), "recipient": str(self.recipient.sqid)},
            self.manager,
        ))["passProposalRoundClarification"]
        self.assertFalse(payload["ok"])
        self.assertIn("hidden asker", str(payload["validationErrors"]))
        self.assertEqual(self.Task._base_manager.filter(pk=question.pk).values().get(), before)

    def test_graphql_visibility_refusal_codes(self) -> None:
        blocked = self.question()
        self.post(blocked, self.asker)
        published = self.question(restricted=False, manager=True, responders=True)
        for question, visibility, code in (
            (blocked, "INHERITED", "HIDDEN_ASKER_IN_THREAD"),
            (published, "RESTRICTED", "PUBLISHED_QUESTION"),
        ):
            with self.subTest(code=code):
                payload = self.data(self.execute(
                    """mutation($id: ID!, $visibility: TaskVisibility!) {
                      setTaskVisibility(id: $id, visibility: $visibility) { ok code }
                    }""",
                    {"id": str(question.sqid), "visibility": visibility}, self.manager,
                ))["setTaskVisibility"]
                self.assertEqual(payload, {"ok": False, "code": code})

    def test_graphql_post_refuses_hidden_asker_in_band(self) -> None:
        question = self.question(restricted=False)
        with actor_context(self.manager):
            self.as_user(self.round, self.manager).pass_clarification(question, self.recipient)
        before = self.Message._base_manager.count()
        for kind in ("comment", "note"):
            with self.subTest(kind=kind):
                payload = self.data(self.execute(
                    """mutation($id: ID!, $kind: String!) {
                      post_record_message(input: {
                        model_label: "projects.Task", record_id: $id, body: "Hidden asker", kind: $kind
                      }) { message { id } error error_code }
                    }""",
                    {"id": str(question.sqid), "kind": kind}, self.asker,
                ))["post_record_message"]
                self.assertIsNone(payload["message"])
                self.assertIn("hidden asker", payload["error"])
                self.assertEqual(self.Message._base_manager.count(), before)
                self.assertEqual(payload["error_code"], "HIDDEN_ASKER_IN_THREAD")


class RegistryPublicationTests(PublicationCases):
    """D26 under the composed host's FK-backed relationship store."""


class DenormalizedPublicationTests(PublicationCases):
    """The same D26 behavior under the alternate relationship store."""

    storage = "denormalized"
