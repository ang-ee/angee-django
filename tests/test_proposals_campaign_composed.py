"""GraphQL, publication and optional bridge regressions on the shared emitted host."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import connection
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from graphql import GraphQLInputObjectType, GraphQLObjectType, get_named_type
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import GraphQLSchemas
from angee.proposals.models import ClarificationWidenBlocked, PublishedQuestion
from tests.composed_host import run_composed_tests
from tests.test_proposals_clarifications import ClarificationCase
from tests.test_proposals_work import ProposalsWorkTests


class CampaignIdentities(TransactionTestCase):
    """Keep the shared composed fixture, with deterministic per-test identities."""

    def setUp(self) -> None:
        prefix = hashlib.sha256(self.id().encode()).hexdigest()[:12]
        factory = get_user_model().objects.create_person_as_system

        def person(_manager: Any, **kwargs: Any) -> Any:
            kwargs["username"] = f"{prefix}-{kwargs['username']}"
            kwargs["email"] = f"{kwargs['username']}@example.test"
            return factory(**kwargs)

        self.enterContext(patch.object(timezone, "now", return_value=datetime(2032, 3, 4, 12, tzinfo=UTC)))
        with patch.object(type(get_user_model().objects), "create_person_as_system", person):
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


class ProposalCampaignCases(CampaignIdentities, ClarificationCase):
    """Both queue-free profiles consume the real proposal/task/schema owners."""

    __test__ = False
    with_work = False

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

    def test_reader_sees_lifted_state_and_date(self) -> None:
        with system_context(reason="tests.proposals.campaign.lift_before_phase"):
            round = self.Round.objects.get(pk=self.round.pk)
            round.opens_after = round.clarifications_shared_until
            round.save(update_fields=("opens_after",))
        with actor_context(self.manager):
            self.assertTrue(self.as_user(self.round, self.manager).can_open())
            self.as_user(self.round, self.manager).open()
        result = self.execute_named(
            """query($id: String!) {
              proposal_rounds_by_pk(id: $id) { status opened_at can_open }
            }""",
            {"id": str(self.round.sqid)},
            self.asker,
            "console",
        )
        row = self.data(result)["proposal_rounds_by_pk"]
        self.assertEqual(row["status"], "OPENED")
        self.assertIsNotNone(row["opened_at"])
        self.assertFalse(row["can_open"])

    def test_canceled_round_can_lift_without_reopening(self) -> None:
        with actor_context(self.manager):
            self.as_user(self.round, self.manager).cancel()
            self.assertTrue(self.as_user(self.round, self.manager).can_open())
            lifted = self.as_user(self.round, self.manager).open()
        self.assertEqual(lifted.status, "canceled")
        self.assertIsNotNone(lifted.opened_at)
        result = self.execute_named(
            """query($id: String!) {
              proposal_rounds_by_pk(id: $id) { status opened_at }
            }""",
            {"id": str(self.round.sqid)},
            self.asker,
            "console",
        )
        row = self.data(result)["proposal_rounds_by_pk"]
        self.assertEqual(row["status"], "CANCELED")
        self.assertIsNotNone(row["opened_at"])

    def test_project_editor_cannot_comment_on_widened_question(self) -> None:
        with actor_context(self.manager):
            self.as_user(self.project, self.manager).grant_record_access("editor", self.member)
        task = self.ask()
        held = self.as_user(task, self.member)
        self.assertTrue(held.has_access("read"))
        self.assertFalse(held.has_access("comment"))
        with actor_context(self.member), self.assertRaises(PermissionDenied):
            held.message_post("Not addressed to me")

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

    def test_optional_profile_has_no_bridge_column_or_personal_question_queue(self) -> None:
        self.assertEqual("work" in apps.app_configs, self.with_work)
        self.assertNotIn("proposals_work", apps.app_configs)
        with self.assertRaises(FieldDoesNotExist):
            self.Round._meta.get_field("clarification_queue")
        task = self.ask()
        self.assert_queue_free(task)
        for name in ("public", "console"):
            schema = GraphQLSchemas.from_discovery().build(name)
            root = schema._schema.query_type
            assert root is not None
            node = get_named_type(root.fields["proposal_rounds"].type)
            assert isinstance(node, GraphQLObjectType)
            self.assertNotIn("clarification_queue", node.fields)

    def test_publication_guard_blocks_model_and_graphql_widening_without_a_partial_pass(self) -> None:
        task = self.restricted_question()
        with actor_context(self.asker):
            message = self.as_user(task, self.asker).message_post("Private asker comment")
        self.assertEqual(message.created_by_id, self.asker.pk)
        with system_context(reason="tests.proposals.campaign.widen_exists"):
            task = self.Task.objects.get(pk=task.pk)
            with CaptureQueriesContext(connection) as queries, self.assertRaises(ClarificationWidenBlocked) as error:
                task.validate_visibility("inherited")
            self.assertEqual(error.exception.code, "HIDDEN_ASKER_IN_THREAD")
            # Auditing the two task scopes and the message scope adds three
            # INSERTs; the publication check itself remains one SELECT.
            self.assertEqual(len(queries), 4, queries.captured_queries)
            self.assertTrue(all('INSERT INTO "rebac_permissionauditevent"' in q["sql"] for q in queries[:3]))
            self.assertIn("EXISTS", queries[3]["sql"].upper())
        before = self.Task._base_manager.filter(pk=task.pk).values().get()
        with actor_context(self.manager):
            with self.assertRaises(ClarificationWidenBlocked):
                self.as_user(task, self.manager).set_visibility("inherited")
            with self.assertRaises(ClarificationWidenBlocked):
                self.as_user(self.round, self.manager).pass_clarification(task, self.recipient)
        operations = (
            (
                "set_task_visibility",
                """mutation($task: ID!) {
              set_task_visibility(id: $task, visibility: INHERITED) { ok code }
            }""",
                {"task": str(task.sqid)},
            ),
            (
                "pass_proposal_round_clarification",
                """mutation($round: ID!, $task: ID!, $recipient: ID!) {
              pass_proposal_round_clarification(round: $round, task: $task, recipient: $recipient) { ok code }
            }""",
                {"round": str(self.round.sqid), "task": str(task.sqid), "recipient": str(self.recipient.sqid)},
            ),
        )
        for root, document, variables in operations:
            with self.subTest(root=root):
                payload = self.data(self.execute(document, variables, self.manager))[root]
                self.assertEqual(payload, {"ok": False, "code": "HIDDEN_ASKER_IN_THREAD"})
                self.assertEqual(self.Task._base_manager.filter(pk=task.pk).values().get(), before)

    def test_published_question_cannot_narrow_and_hidden_asker_cannot_post_by_either_api(self) -> None:
        task = self.restricted_question()
        stale = self.as_user(task, self.asker)
        with actor_context(self.manager):
            passed = self.as_user(self.round, self.manager).pass_clarification(task, self.recipient)
            with self.assertRaises(PublishedQuestion) as error:
                self.as_user(passed, self.manager).set_visibility("restricted")
            self.assertEqual(error.exception.code, "PUBLISHED_QUESTION")
        before = apps.get_model("messaging", "Message")._base_manager.count()
        with actor_context(self.asker):
            for method in (stale.message_post, stale.message_log):
                with self.assertRaises(ClarificationWidenBlocked):
                    method("Must not disclose the asker")
        narrowed = self.data(
            self.execute(
                """mutation($id: ID!) {
              set_task_visibility(id: $id, visibility: RESTRICTED) { ok code }
            }""",
                {"id": str(task.sqid)},
                self.manager,
            )
        )["set_task_visibility"]
        self.assertEqual(narrowed, {"ok": False, "code": "PUBLISHED_QUESTION"})
        for kind in ("comment", "note"):
            result = self.data(
                self.execute(
                    """mutation($id: ID!, $kind: RecordMessagePostKind!) {
                  post_record_message(input: {model_label: "projects.Task", record_id: $id,
                    body: "Must not disclose the asker", kind: $kind}) { message { id } error_code }
                }""",
                    {"id": str(task.sqid), "kind": kind.upper()},
                    self.asker,
                )
            )["post_record_message"]
            self.assertEqual(result, {"message": None, "error_code": "HIDDEN_ASKER_IN_THREAD"})
        self.assertEqual(apps.get_model("messaging", "Message")._base_manager.count(), before)
        self.assertEqual(self.Task._base_manager.get(pk=task.pk).visibility, "inherited")

    def test_named_asker_manager_authorship_and_an_empty_thread_can_publish(self) -> None:
        named = self.restricted_question()
        with actor_context(self.asker):
            self.as_user(named, self.asker).message_post("Named question")
        with system_context(reason="tests.proposals.campaign.named_asker"):
            row = self.Round.objects.get(pk=self.round.pk)
            row.clarification_askers = "named"
            row.save(update_fields=("clarification_askers",))
        with actor_context(self.manager):
            self.as_user(named, self.manager).set_visibility("inherited")
            manager_question = self.as_user(self.round, self.manager).ask(
                "Manager", "Details", recipient=self.recipient
            )
            self.as_user(manager_question, self.manager).message_post("Manager comment")
            self.as_user(manager_question, self.manager).set_visibility("inherited")
        with system_context(reason="tests.proposals.campaign.hidden_again"):
            row = self.Round.objects.get(pk=self.round.pk)
            row.clarification_askers = "hidden"
            row.save(update_fields=("clarification_askers",))
        empty = self.restricted_question()
        with actor_context(self.manager):
            self.as_user(empty, self.manager).set_visibility("inherited")

    def test_all_recipient_question_is_born_published_and_cannot_be_narrowed_while_waiting(self) -> None:
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).ask("All responders", "Reply", recipient="responders")
            self.assertEqual(task.visibility, "inherited")
            self.assertIsNotNone(task.clarification_passed_at)
            waiting = self.as_user(task, self.manager).clarification_waiting()
            self.assertEqual({entry["id"] for entry in waiting}, {self.asker.pk, self.recipient.pk})
            with self.assertRaises(PublishedQuestion):
                self.as_user(task, self.manager).set_visibility("restricted")
        with actor_context(self.asker):
            self.as_user(task, self.asker).message_post("My answer")
        self.assertEqual(self.Task._base_manager.filter(clarification_round=self.round).count(), 1)

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
            audience: MANAGERS, client_creation_key: "surrender-replay") { ok id code }
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

    def test_generated_writes_reject_stale_round_proposal_and_answer_revisions(self) -> None:
        with system_context(reason="tests.proposals.campaign.generated_revisions"):
            proposal = self.Proposal.objects.get(round=self.round, responder=self.asker)
            topic = apps.get_model("proposals", "Topic").objects.create(round=self.round, key="scope", name="Scope")
            answer = apps.get_model("proposals", "Answer").objects.create(
                proposal=proposal, topic=topic, body="Original"
            )
        for row, root, field, value in (
            (self.round, "proposal_rounds", "name", "Updated round"),
            (proposal, "proposals", "statement", "Updated statement"),
            (answer, "proposal_answers", "body", "Updated answer"),
        ):
            with self.subTest(root=root):
                document = f"""mutation($id: String!, $revision: Int!) {{
                  update_{root}_by_pk(pk_columns: {{id: $id}}, expected_revision: $revision,
                    _set: {{{field}: "{value}"}}) {{ id revision }}
                }}"""
                variables = {"id": str(row.sqid), "revision": row.revision}
                result = self.data(self.execute(document, variables, self.manager))[f"update_{root}_by_pk"]
                self.assertGreater(result["revision"], row.revision)
                stale = self.execute(document, variables, self.manager)
                self.assertTrue(stale.errors)
                self.assertEqual(stale.errors[0].extensions.get("code"), "STALE_REVISION")
                stored = type(row)._base_manager.get(pk=row.pk)
                self.assertEqual(stored.revision, result["revision"])

    def test_generated_proposal_insert_refuses_requester_and_round_managers(self) -> None:
        with system_context(reason="tests.proposals.campaign.requester_insert"):
            party = apps.get_model("parties", "Person").objects.get(user=self.outsider)
            round = self.Round.objects.get(pk=self.round.pk)
            round.requester_party = party
            round.save(update_fields=("requester_party",))
        count = self.Proposal._base_manager.count()
        for actor in (self.outsider, self.manager):
            result = self.execute(
                """mutation($round: ID!, $responder: ID!) {
                  insert_proposals_one(object: {round: $round, responder: $responder}) { id }
                }""",
                {"round": str(self.round.sqid), "responder": str(actor.sqid)},
                self.manager,
            )
            self.assertTrue(result.errors)
            expected = "record_access_subject_refused" if actor == self.outsider else "manager"
            self.assertIn(expected, str(result.errors).lower())
        self.assertEqual(self.Proposal._base_manager.count(), count)

    def test_offer_and_round_setting_masking_survive_publication_in_both_schemas(self) -> None:
        with system_context(reason="tests.proposals.campaign.offer"):
            row = self.Proposal.objects.get(round=self.round, responder=self.asker)
            row.statement = "Sealed commitment"
            row.staffing = "Two people"
            row.save(update_fields=("statement", "staffing"))
        for roster in ("hidden", "named"):
            with system_context(reason="tests.proposals.campaign.roster"):
                round = self.Round.objects.get(pk=self.round.pk)
                round.roster_visibility = roster
                round.save(update_fields=("roster_visibility",))
            for opened in (False, True):
                if opened:
                    with actor_context(self.manager):
                        current = self.as_user(self.round, self.manager)
                        if current.status == "collecting":
                            # Default profile uses facilitator_only; monotone widening has its own verb.
                            current.open()
                        self.as_user(self.round, self.manager).widen_opening_policy("drafts_and_tracks")
                for schema in ("public", "console"):
                    own = self.data(
                        self.execute_named(
                            """query($id: String!) { proposals_by_pk(id: $id) { id statement staffing } }""",
                            {"id": str(row.sqid)},
                            self.asker,
                            schema,
                        )
                    )["proposals_by_pk"]
                    self.assertEqual(own["statement"], "Sealed commitment")
                    peer = self.data(
                        self.execute_named(
                            """query($id: String!) { proposals_by_pk(id: $id) { id statement staffing } }""",
                            {"id": str(row.sqid)},
                            self.recipient,
                            schema,
                        )
                    )["proposals_by_pk"]
                    already_open = self.Round._base_manager.get(pk=self.round.pk).opened_at is not None
                    if already_open:
                        self.assertEqual(peer, {"id": str(row.sqid), "statement": None, "staffing": None})
                    else:
                        self.assertIsNone(peer)
                    settings = self.data(
                        self.execute_named(
                            """query($id: String!) { proposal_rounds_by_pk(id: $id) {
                          id roster_visibility clarification_askers opening_policy
                        } }""",
                            {"id": str(self.round.sqid)},
                            self.recipient,
                            schema,
                        )
                    )["proposal_rounds_by_pk"]
                    self.assertIsNone(settings["roster_visibility"])
                    self.assertIsNone(settings["clarification_askers"])
                    self.assertIsNotNone(settings["opening_policy"])

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

    def test_passed_question_content_update_is_refused_and_manager_can_complete(self) -> None:
        """D25 reserves stage exits for managers; D35 leaves recipients discussion-only."""
        task = self.ask()
        with actor_context(self.manager):
            task = self.as_user(self.round, self.manager).pass_clarification(task, self.recipient, audience="asker")
        result = self.execute(
            """mutation($id: String!) {
              update_project_tasks_by_pk(pk_columns: {id: $id}, _set: {title: "Rewritten answer context"}) { id }
            }""",
            {"id": str(task.sqid)},
            self.manager,
        )
        self.assertTrue(result.errors)
        self.assertEqual(self.Task._base_manager.get(pk=task.pk).title, "Question")
        refused = self.execute(
            "mutation($id: ID!) { complete_task(id: $id) { ok } }",
            {"id": str(task.sqid)},
            self.recipient,
        )
        self.assertIsNone(refused.errors, refused.errors)
        self.assertFalse(refused.data["complete_task"]["ok"])
        completed = self.task_action("complete_task", task, self.manager)
        self.assertEqual(completed.status, "done")
        with actor_context(self.recipient), self.assertRaises(PermissionDenied):
            row = self.as_user(task, self.recipient)
            row.assignee = self.outsider
            row.save(update_fields=("assignee",))

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

    def test_restricted_question_and_its_reply_are_absent_from_peer_lists_and_aggregates(self) -> None:
        task = self.restricted_question()
        with actor_context(self.manager):
            self.as_user(task, self.manager).message_post("Private answer")
        for name in ("public", "console"):
            data = self.data(
                self.execute_named(
                    """query {
              project_tasks { id parent { id } }
              project_tasks_aggregate { aggregate { count } }
              project_tasks_groups(group_by: [{field: CLARIFICATION_ROUND}]) { aggregate { count } }
            }""",
                    {},
                    self.recipient,
                    name,
                )
            )
            self.assertEqual(data["project_tasks"], [])
            self.assertEqual(data["project_tasks_aggregate"]["aggregate"]["count"], 0)
            self.assertEqual(data["project_tasks_groups"], [])
        with actor_context(self.recipient):
            messages = self.Task.thread_messages_expression(task.pk).as_user(self.recipient)
            self.assertFalse(messages.exists())


class RegistryProposalCampaign(ProposalCampaignCases):
    pass


class DenormalizedProposalCampaign(ProposalCampaignCases):
    storage = "denormalized"


class RegistryWorkWithoutBridgeCampaign(ProposalCampaignCases):
    with_work = True


class DenormalizedWorkWithoutBridgeCampaign(RegistryWorkWithoutBridgeCampaign):
    storage = "denormalized"


class RegistryBridgeCampaign(CampaignIdentities, ProposalsWorkTests):
    """Retain the existing full bridge cases and add deletion/rebinding regressions."""

    __test__ = False

    def test_unpublished_track_refusal_rolls_back_new_task_chatter_and_revision(self) -> None:
        _proposal, track = self.private_track()
        messages = apps.get_model("messaging", "Message")
        before = messages._base_manager.count()
        with system_context(reason="tests.proposals.campaign.track_queue"), self.assertRaises(ValidationError):
            self.Task.objects.create(project=track, queue=self.queue, title="Must remain private")
        self.assertFalse(self.Task._base_manager.filter(project=track).exists())
        self.assertEqual(messages._base_manager.count(), before)

    def test_routing_queue_cannot_be_deleted_through_its_group_and_clearing_releases_protection(self) -> None:
        self.route()
        with system_context(reason="tests.proposals.campaign.queue_delete"):
            with self.assertRaises(ProtectedError):
                apps.get_model("spaces", "Group").objects.get(pk=self.queue.pk).delete()
            row = self.Round.objects.get(pk=self.round.pk)
            row.clarification_queue = None
            row.save(update_fields=("clarification_queue",))
            self.Queue.objects.get(pk=self.queue.pk).delete()
        self.assertFalse(self.Queue._base_manager.filter(pk=self.queue.pk).exists())

    def test_hidden_and_published_questions_follow_native_asked_and_passed_routing(self) -> None:
        self.route()
        hidden = self.ask(audience="managers")
        self.assertEqual((hidden.queue_id, hidden.stage_id), (self.queue.pk, self.ready.pk))
        with actor_context(self.member):
            self.assertFalse(self.as_user(hidden, self.member).has_access("read"))
        with actor_context(self.manager):
            published = self.as_user(self.round, self.manager).ask("All responders", "Reply", recipient="responders")
        self.assertEqual((published.queue_id, published.stage_id), (self.queue.pk, self.started.pk))
        self.assertIsNotNone(published.clarification_passed_at)
        with actor_context(self.member), self.assertRaises(PermissionDenied):
            self.as_user(published, self.member).message_post("Queue member response")


class DenormalizedBridgeCampaign(RegistryBridgeCampaign):
    storage = "denormalized"


@pytest.mark.parametrize(
    "profile,case",
    (
        (("angee.proposals",), "RegistryProposalCampaign"),
        (("angee.proposals",), "DenormalizedProposalCampaign"),
        (("angee.proposals", "angee.work"), "RegistryWorkWithoutBridgeCampaign"),
        (("angee.proposals", "angee.work"), "DenormalizedWorkWithoutBridgeCampaign"),
        (("angee.proposals_work",), "RegistryBridgeCampaign"),
        (("angee.proposals_work",), "DenormalizedBridgeCampaign"),
    ),
    ids=(
        "registry-proposals",
        "denormalized-proposals",
        "registry-work",
        "denormalized-work",
        "registry-bridge",
        "denormalized-bridge",
    ),
)
def test_proposal_campaign_on_composed_profiles(tmp_path: Path, profile: tuple[str, ...], case: str) -> None:
    """Exercise proposal behavior under each composed addon profile."""
    run_composed_tests(tmp_path, f"tests.test_proposals_campaign_composed.{case}", app=profile)
