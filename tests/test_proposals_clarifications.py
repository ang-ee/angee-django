"""Question lifecycle, GraphQL field gates and aggregate scope of proposals on emitted models, with and without Work."""

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import FieldDoesNotExist
from django.core.management import call_command
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone
from graphql import GraphQLInputObjectType, get_named_type
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_object_ref,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import graphql_request


class ClarificationCase(TransactionTestCase):
    """Shared native proposal fixture; no hand-built donor or permission graph."""

    __test__ = False

    def setUp(self):
        settings = override_settings(
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
        request = graphql_request(user)
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
        task = self.ask(idempotency_key="question")
        self.assertEqual(self.ask(idempotency_key="question").pk, task.pk)
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


class CampaignIdentities(TransactionTestCase):
    """Keep the shared composed fixture, with deterministic per-test identities."""

    def setUp(self) -> None:
        prefix = hashlib.sha256(self.id().encode()).hexdigest()[:12]
        factory = get_user_model().objects.create_person_as_system

        def person(**kwargs: Any) -> Any:
            kwargs["username"] = f"{prefix}-{kwargs['username']}"
            kwargs["email"] = f"{kwargs['username']}@example.test"
            return factory(**kwargs)

        self.enterContext(patch.object(timezone, "now", return_value=datetime(2032, 3, 4, 12, tzinfo=UTC)))
        with patch.object(get_user_model().objects, "create_person_as_system", person):
            super().setUp()
        # User pickers must resolve named recipients before the action reaches its
        # proposal guard. This grants directory reads, never administrator reach.
        with system_context(reason="tests.proposals.campaign.directory"):
            write_relationships(
                [
                    RelationshipTuple(ObjectRef("iam/directory", "main"), "reader", to_subject_ref(actor))
                    for actor in (self.manager, self.asker, self.recipient, self.member, self.outsider)
                ]
            )


class ClarificationProfileTests(CampaignIdentities, ClarificationCase):
    """Both queue-free profiles run every clarification case, field gate and aggregate scope once."""

    def test_bridge_is_optional(self):
        with self.assertRaises(FieldDoesNotExist):
            self.Round._meta.get_field("clarification_queue")
        self.assertNotIn("proposals_work", apps.app_configs)
        # Both profiles must build their complete public and console schemas.
        schemas = GraphQLSchemas.from_discovery().render_sdl()
        self.assertTrue(schemas)
        self.assertTrue(all("clarification_queue" not in sdl for sdl in schemas.values()))


    def test_permission_checks_after_sync(self) -> None:
        """Every composed profile passes the database-aware permission checks."""

        failures = [
            issue.id
            for issue in checks.run_checks(databases=["default"])
            if issue.id.startswith("rebac.E01")
        ]
        self.assertEqual(failures, [])

    def data(self, result: Any) -> dict[str, Any]:
        self.assertIsNone(result.errors, result.errors)
        self.assertIsNotNone(result.data)
        return result.data

    def execute_named(self, document: str, variables: dict[str, Any], actor: Any, name: str) -> Any:
        request = RequestFactory().post(f"/graphql/{name}/")
        request.user = actor
        with actor_context(actor):
            return (
                GraphQLSchemas.from_discovery()
                .build(name)
                .execute_sync(
                    document,
                    variable_values=variables,
                    context_value=SimpleNamespace(request=request),
                )
            )

    def restricted_question(self) -> Any:
        task = self.ask()
        with actor_context(self.manager):
            self.as_user(task, self.manager).set_visibility("restricted")
        return self.as_user(task, self.asker)

    def test_graphql_blocker_and_asker_are_private_and_question_audit_stays_anonymous(self) -> None:
        task = self.restricted_question()
        with actor_context(self.asker):
            comment = self.as_user(task, self.asker).message_post("Hidden speaker")
        with actor_context(self.manager):
            self.as_user(task, self.manager).grant_record_access("reader", self.recipient)
        for name in ("public", "console"):
            for actor in (self.manager, self.recipient):
                result = self.data(
                    self.execute_named(
                        """query($id: String!) { project_tasks_by_pk(id: $id) {
                      id clarification_widen_blocker clarification_asker { id }
                    } }""",
                        {"id": str(task.sqid)},
                        actor,
                        name,
                    )
                )["project_tasks_by_pk"]
                self.assertEqual(
                    result["clarification_widen_blocker"], "hidden_asker_message" if actor == self.manager else None
                )
                self.assertEqual(
                    result["clarification_asker"], {"id": str(self.asker.sqid)} if actor == self.manager else None
                )
        stored = self.Task._base_manager.get(pk=task.pk)
        self.assertIsNone(stored.created_by_id)
        self.assertIsNone(stored.owner_id)
        with system_context(reason="tests.proposals.campaign.audit"):
            creation = self.Task.thread_messages_expression(task.pk).exclude(pk=comment.pk)
            self.assertFalse(creation.filter(created_by_id=self.asker.pk).exists())

    def test_surrendered_graphql_replay_returns_only_the_original_id_and_conflicts_on_changed_body(self) -> None:
        document = """mutation($round: ID!, $body: String!) {
          ask_proposal_round(round: $round, title: "Private question", body: $body,
            audience: MANAGERS, idempotency_key: "surrender-replay") { ok id code }
        }"""
        variables = {"round": str(self.round.sqid), "body": "Original"}
        first = self.data(self.execute(document, variables, self.asker))["ask_proposal_round"]
        replay = self.data(self.execute(document, variables, self.asker))["ask_proposal_round"]
        self.assertTrue(first["ok"])
        self.assertEqual(first, replay)
        hidden = self.data(
            self.execute(
                "query($id: String!) { project_tasks_by_pk(id: $id) { id note } }",
                {"id": first["id"]},
                self.asker,
            )
        )["project_tasks_by_pk"]
        self.assertIsNone(hidden)
        mismatch = self.execute(document, {**variables, "body": "Changed"}, self.asker)
        self.assertIsNotNone(mismatch.errors)
        self.assertEqual(len(mismatch.errors), 1)
        self.assertEqual(mismatch.errors[0].extensions["code"], "CREATION_KEY_CONFLICT")
        self.assertEqual(self.Task._base_manager.filter(clarification_round=self.round).count(), 1)

    def test_offer_and_round_setting_field_gates_mask_peers_in_both_schemas(self) -> None:
        """The GraphQL field gate: a disclosed peer row arrives without its offer, and no round setting."""
        with system_context(reason="tests.proposals.campaign.offer"):
            row = self.Proposal.objects.get(round=self.round, responder=self.asker)
            row.statement = "Sealed commitment"
            row.staffing = "Two people"
            row.save(update_fields=("statement", "staffing"))
        proposal = """query($id: String!) { proposals_by_pk(id: $id) { id statement staffing } }"""
        settings = """query($id: String!) { proposal_rounds_by_pk(id: $id) {
          id roster_visibility clarification_askers opening_policy
        } }"""
        for opened in (False, True):
            if opened:
                with actor_context(self.manager):
                    self.as_user(self.round, self.manager).open()
                    self.as_user(self.round, self.manager).widen_opening_policy("drafts_and_tracks")
            for schema in ("public", "console"):
                variables = {"id": str(row.sqid)}
                own = self.data(self.execute_named(proposal, variables, self.asker, schema))["proposals_by_pk"]
                self.assertEqual(own["statement"], "Sealed commitment")
                peer = self.data(self.execute_named(proposal, variables, self.recipient, schema))["proposals_by_pk"]
                self.assertEqual(peer, {"id": str(row.sqid), "statement": None, "staffing": None} if opened else None)
                round = self.data(
                    self.execute_named(settings, {"id": str(self.round.sqid)}, self.recipient, schema)
                )["proposal_rounds_by_pk"]
                self.assertIsNone(round["roster_visibility"])
                self.assertIsNone(round["clarification_askers"])
                self.assertIsNotNone(round["opening_policy"])

    def test_gated_fields_are_absent_from_query_axes_and_share_columns_from_generated_writes(self) -> None:
        schema = GraphQLSchemas.from_discovery().build("console")._schema
        mutation = schema.mutation_type
        query = schema.query_type
        assert mutation is not None
        assert query is not None
        for resource, gated in (("proposals", "statement"), ("project_tasks", "clarification_asker")):
            for argument in ("where", "order_by"):
                input_type = get_named_type(query.fields[resource].args[argument].type)
                assert isinstance(input_type, GraphQLInputObjectType)
                self.assertNotIn(gated, input_type.fields)
        for resource in ("project_tasks", "proposal_answers"):
            for verb, argument in (("insert", "object"), ("update", "_set")):
                suffix = "one" if verb == "insert" else "by_pk"
                input_type = get_named_type(mutation.fields[f"{verb}_{resource}_{suffix}"].args[argument].type)
                assert isinstance(input_type, GraphQLInputObjectType)
                self.assertNotIn("shared_with_responders", input_type.fields)

    def test_permissions_fields_report_native_actions_for_the_manager_and_responder(self) -> None:
        with system_context(reason="tests.proposals.campaign.permissions"):
            proposal = self.Proposal.objects.get(round=self.round, responder=self.asker)
            topic = apps.get_model("proposals", "Topic").objects.create(round=self.round, key="actions", name="Actions")
            answer = apps.get_model("proposals", "Answer").objects.create(proposal=proposal, topic=topic)
        for actor, expected in (
            (
                self.manager,
                (
                    {"manage", "write", "ask"},
                    {"write", "publish", "withdraw", "share", "read_offer"},
                    {"write", "narrow", "manage"},
                ),
            ),
            (self.asker, ({"respond", "ask"}, {"write", "withdraw", "read_offer"}, {"write", "narrow"})),
        ):
            for name in ("public", "console"):
                data = self.data(
                    self.execute_named(
                        """query($round: String!, $proposal: String!, $answer: String!) {
                      proposal_rounds_by_pk(id: $round) { permissions }
                      proposals_by_pk(id: $proposal) { permissions }
                      proposal_answers_by_pk(id: $answer) { permissions }
                    }""",
                        {"round": str(self.round.sqid), "proposal": str(proposal.sqid), "answer": str(answer.sqid)},
                        actor,
                        name,
                    )
                )
                actual = tuple(
                    set(data[root]["permissions"])
                    for root in ("proposal_rounds_by_pk", "proposals_by_pk", "proposal_answers_by_pk")
                )
                self.assertEqual(actual, expected, repr(actual))

    def test_named_roster_and_sealed_answers_do_not_leak_through_lists_aggregates_or_groups(self) -> None:
        with system_context(reason="tests.proposals.campaign.aggregate_setup"):
            round = self.Round.objects.get(pk=self.round.pk)
            round.roster_visibility = "named"
            round.save(update_fields=("roster_visibility",))
            proposal = self.Proposal.objects.get(round=round, responder=self.asker)
            topic = apps.get_model("proposals", "Topic").objects.create(round=round, key="private", name="Private")
            apps.get_model("proposals", "Answer").objects.create(
                proposal=proposal,
                topic=topic,
                body="Managers only",
                visibility="sealed",
            )
        for opened in (False, True):
            if opened:
                with actor_context(self.manager):
                    self.as_user(self.round, self.manager).open()
                    self.as_user(self.round, self.manager).widen_opening_policy("drafts_and_tracks")
            for name in ("public", "console"):
                data = self.data(
                    self.execute_named(
                        """query {
                  proposals { id }
                  proposals_aggregate { aggregate { count } }
                  proposals_groups(group_by: [{field: ROUND}]) { aggregate { count } }
                  proposal_answers { id proposal { id } }
                  proposal_answers_aggregate { aggregate { count } }
                  proposal_answers_groups(group_by: [{field: PROPOSAL}]) { aggregate { count } }
                }""",
                        {},
                        self.recipient,
                        name,
                    )
                )
                expected = 2 if opened else 1
                self.assertEqual(len(data["proposals"]), expected)
                self.assertEqual(data["proposals_aggregate"]["aggregate"]["count"], expected)
                self.assertEqual(data["proposals_groups"], [{"aggregate": {"count": expected}}])
                self.assertEqual(data["proposal_answers"], [])
                self.assertEqual(data["proposal_answers_aggregate"]["aggregate"]["count"], 0)
                self.assertEqual(data["proposal_answers_groups"], [])


class ProposalsOnlyTests(ClarificationProfileTests):
    def test_work_is_absent(self):
        self.assertNotIn("work", apps.app_configs)


class WithWorkTests(ClarificationProfileTests):
    def test_work_is_present(self):
        self.assertIn("work", apps.app_configs)
