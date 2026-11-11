"""Composed decision links inherit execution visibility and native query axes."""

import pytest
from rebac import RelationshipTuple, to_object_ref, to_subject_ref, write_relationships

from angee.decisions import schema as decision_schema
from angee.decisions.contracts import DecisionProposal, DecisionRequest
from angee.decisions.testing.models import Decision
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows import schema as workflow_schema
from angee.workflows.decision_steps import DecisionStep
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import SchemaAddon, create_user, vault_for
from tests.test_workflows_run_graph import graph_nodes


@pytest.fixture
def linked_decision(execution, register_step):
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
    step = StepRun.system_queryset().get(run=run)
    assert (step.status, step.waiting_kind) == ("waiting", "decision")
    decision = Decision.system_queryset().get(pk=step.decision_id)
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


def test_participants_and_step_operators_read_the_decision(schema, linked_decision):
    workflow, run, step, decision, owner, operator, stranger, assignee = linked_decision
    query = "{ steprun { id decision { id is_open permissions } } decisions { id } }"
    for viewer in (owner, operator):
        data = result_data(execute_schema(schema, query, user=viewer))
        assert data["steprun"][0]["decision"]["id"] == decision.sqid
        assert data["steprun"][0]["decision"]["is_open"]
        assert "act" not in data["steprun"][0]["decision"]["permissions"]
        assert data["decisions"] == [{"id": decision.sqid}]
    assert result_data(execute_schema(schema, query, user=stranger)) == {"steprun": [], "decisions": []}
    run.with_actor(owner).grant_record_access("reader", stranger)
    assert result_data(execute_schema(schema, query, user=stranger)) == {
        "steprun": [{"id": step.sqid, "decision": None}], "decisions": [],
    }
    data = result_data(execute_schema(schema, query, user=assignee))
    assert data["steprun"] == [] and data["decisions"] == [{"id": decision.sqid}]


def test_step_decision_filter_uses_public_identity(schema, linked_decision):
    _, _, step, decision, owner, *_ = linked_decision
    query = "query($id: String!) { steprun(where: {decision: {_eq: $id}}) { id } }"
    assert result_data(execute_schema(schema, query, {"id": decision.sqid}, user=owner)) == {
        "steprun": [{"id": step.sqid}],
    }


@pytest.mark.parametrize("run_first", [False, True])
def test_decision_waiting_runs_compose_node_labels_and_nested_run_selection(schema, linked_decision, run_first):
    workflow, run, step, decision, owner, _, stranger, assignee = linked_decision
    fields = [
        "id node_label map_index is_mapped status",
        "run { id display_name version { workflow { display_name } } }",
    ]
    if run_first:
        fields.reverse()
    query = """query DecisionSteps($decision: String!) {
      steprun(where: {decision: {_eq: $decision}}, order_by: [{rank: asc}, {map_index: asc}]) {
    """ + " ".join(fields) + "} }"
    run.with_actor(owner).grant_record_access("reader", assignee)
    for viewer in (owner, assignee):
        data = result_data(execute_schema(schema, query, {"decision": decision.sqid}, user=viewer))
        assert data["steprun"] == [{
            "id": step.sqid, "node_label": step.node_label, "map_index": step.map_index,
            "is_mapped": False, "status": "WAITING",
            "run": {"id": run.sqid, "display_name": str(run), "version": (
                {"workflow": {"display_name": workflow.name}} if viewer == owner else None
            )},
        }]
    assert result_data(execute_schema(schema, query, {"decision": decision.sqid}, user=stranger)) == {"steprun": []}


def test_decide_returns_the_answered_decision_for_exact_cache_adoption(schema, linked_decision):
    _, _, _, decision, _, _, _, assignee = linked_decision
    query = """mutation($id: ID!, $revision: Int!, $chosen: [String!]!) {
      decide(id: $id, revision: $revision, chosen: $chosen) {
        ok decision {
          id kind kind_label revision created_at is_open permissions context proposal
          verdict verdict_values verdict_label answered_at
          records { id record_model record_id }
          requester { display_name }
          assignees { display_name }
          answered_by { display_name }
        }
      }
    }"""
    variables = {"id": decision.sqid, "revision": decision.revision, "chosen": ["accept"]}
    refused = result_data(execute_schema(schema, query, {**variables, "chosen": ["unknown"]}, user=assignee))["decide"]
    assert refused == {"ok": False, "decision": None}
    answered = result_data(execute_schema(schema, query, variables, user=assignee))["decide"]
    assert answered["ok"]
    assert answered["decision"]["id"] == decision.sqid
    assert answered["decision"]["revision"] == decision.revision + 1
    assert answered["decision"]["is_open"] is False
    assert answered["decision"]["verdict"] == ["accept"]
    assert answered["decision"]["verdict_label"] == "Accept"
    assert answered["decision"]["answered_at"] and answered["decision"]["answered_by"]["display_name"]


def test_record_timeline_reads_the_asking_run_and_its_question(schema, linked_decision):
    _, run, step, decision, owner, *_ = linked_decision
    link = step.records.model.system_queryset().get(step_run=step)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records) { open_decision_count records {
        record_model record_id decisions { id } runs { id graph { nodes { key plan step_run_id } }
          graph_step_runs { id hold decision { id is_open } records { operation record_model record_id } } }
      } }
    }"""
    inputs = {"records": [{"model": link.record_model_label, "id": link.record_public_id}]}
    data = result_data(execute_schema(schema, query, inputs, user=owner))
    entry = data["record_timeline"]["records"][0]
    assert data["record_timeline"]["open_decision_count"] == 1
    assert entry["runs"][0]["id"] == run.sqid
    node = graph_nodes(entry["runs"][0])[0]
    assert node["plan"] == "current"
    assert node["step_run"]["hold"] == "decision"
    assert node["step_run"]["decision"] == {"id": decision.sqid, "is_open": True}
