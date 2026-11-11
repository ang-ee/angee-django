"""GraphQL field gates, aggregate scope and capability projection of proposals on composed profiles."""

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
from django.test import RequestFactory, TransactionTestCase
from django.utils import timezone
from graphql import GraphQLInputObjectType, get_named_type
from rebac import (
    ObjectRef,
    RelationshipTuple,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)

from angee.graphql.schema import GraphQLSchemas
from tests.composed_host import run_composed_tests
from tests.test_proposals_clarifications import ClarificationCase


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

class ProposalCampaign(ProposalCampaignCases):
    pass


class WorkWithoutBridgeCampaign(ProposalCampaignCases):
    with_work = True


@pytest.mark.parametrize(
    "profile,case",
    (
        (("angee.proposals",), "ProposalCampaign"),
        (("angee.proposals", "angee.work"), "WorkWithoutBridgeCampaign"),
    ),
    ids=("proposals", "work"),
)
def test_proposal_campaign_on_composed_profiles(tmp_path: Path, profile: tuple[str, ...], case: str) -> None:
    """Exercise proposal behavior under each composed addon profile."""
    run_composed_tests(tmp_path, f"tests.test_proposals_campaign_composed.{case}", app=profile)
