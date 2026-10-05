"""Composed decision links inherit execution visibility and native query axes."""

import pytest
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.base.scoping import system_queryset
from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.schema import GraphQLSchemas
from angee.workflows import schema as workflow_schema
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import SchemaAddon, create_user, execute_schema, result_data, vault_for


@pytest.fixture
def linked_decision(execution, register_step, composed_permissions):
    """Compose permission contributions and reach a real review's decision wait."""

    admin, _sent = execution
    owner, operator, stranger, assignee = (
        create_user(name) for name in ("run-owner", "run-operator", "other-starter", "seat-assignee")
    )

    reference = vault_for(owner)
    write_relationships([RelationshipTuple(to_object_ref(reference), "viewer", to_subject_ref(assignee))])

    class Question(DecisionStep[None, None, None]):
        """Ask through the worker boundary that admits and links decision groups."""

        key = "linked_review"
        outcomes = {"accepted": "Accepted"}

        def ask(self, ctx):
            """Offer one seat to a person with no execution grants."""
            return ctx.ask(
                DecisionRequest(
                    kind="review",
                    records=(reference,),
                    assignees=(assignee,),
                    requester=owner,
                    proposal=DecisionProposal(
                        alternatives=[{"key": "accept", "label": "Accept", "outcome": "accepted"}]
                    ),
                )
            )

        def continue_with(self, ctx, decision, outcome):
            """Complete through the same declared action outcome."""
            return ctx.done(outcome=outcome)

    register_step(Question)
    workflow = load_workflow(
        {
            "nodes": {"entry": {"step": Question.key}},
            "results": [{"from": "entry", "when": ["accepted"], "as": "accepted"}],
        },
        actor=admin,
    )
    for person in (owner, stranger):
        workflow.with_actor(admin).grant_record_access("starter", person)
    run = start_run(workflow, actor=owner)
    run.with_actor(owner).grant_record_access("operator", operator)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    assert (step.status, step.waiting_kind) == ("waiting", "decision")
    decision = system_queryset(Decision).get(pk=step.decision_id)
    return workflow, run, step, decision, owner, operator, stranger, assignee


@pytest.fixture
def schema():
    """Use both addon buckets including the native output and input donors."""

    return GraphQLSchemas(
        [
            SchemaAddon(decision_schema.schemas),
            SchemaAddon(workflow_schema.schemas),
        ]
    ).build("console")


def test_only_admitted_participants_read_the_step_decision(schema, linked_decision):
    workflow, run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = "{ steprun { id decision { id is_open permissions } } decisions { id } }"
    for viewer in (owner,):
        data = result_data(execute_schema(schema, query, user=viewer))
        assert data["steprun"][0]["decision"]["id"] == decision.sqid
        assert data["steprun"][0]["decision"]["is_open"]
        assert "act" not in data["steprun"][0]["decision"]["permissions"]
    data = result_data(execute_schema(schema, query, user=operator))
    assert data == {"steprun": [{"id": step.sqid, "decision": None}], "decisions": []}
    assert result_data(execute_schema(schema, query, user=stranger)) == {"steprun": [], "decisions": []}
    data = result_data(execute_schema(schema, query, user=assignee))
    assert data["steprun"] == [] and data["decisions"] == [{"id": decision.sqid}]


def test_step_decision_filter_uses_public_identity(schema, linked_decision):
    _, _, step, decision, owner, *_ = linked_decision
    query = "query($id: String!) { steprun(where: {decision: {_eq: $id}}) { id } }"
    assert result_data(execute_schema(schema, query, {"id": decision.sqid}, user=owner)) == {
        "steprun": [{"id": step.sqid}],
    }


def test_record_timeline_reads_the_asking_run_and_its_question(schema, linked_decision):
    _, run, step, decision, owner, *_ = linked_decision
    link = system_queryset(step.records.model).get(step_run=step)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records) { open_decision_count records {
        record_model record_id decisions { id } runs { id graph { nodes {
          key plan step_run { id hold decision { id is_open } records { operation record_model record_id } }
        } } }
      } }
    }"""
    inputs = {"records": [{"model": link.record_model_label, "id": link.record_public_id}]}
    data = result_data(execute_schema(schema, query, inputs, user=owner))
    entry = data["record_timeline"]["records"][0]
    assert data["record_timeline"]["open_decision_count"] == 1
    assert entry["runs"][0]["id"] == run.sqid
    node = entry["runs"][0]["graph"]["nodes"][0]
    assert node["plan"] == "current"
    assert node["step_run"]["hold"] == "decision"
    assert node["step_run"]["decision"] == {"id": decision.sqid, "is_open": True}
