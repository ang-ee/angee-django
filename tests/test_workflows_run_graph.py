"""Run-pinned graph reads, bounded map aggregates and reader authorization."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from angee.base.scoping import system_queryset
from angee.workflows import schema as workflow_schema
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.workflow_steps import Route

pytestmark = pytest.mark.usefixtures("workflow_step_classes")

GRAPH = """query($id: String!) {
  workflowrun_by_pk(id: $id) {
    id version { id }
    graph {
      nodes {
        key label step step_label rank body_key outcomes { id label }
        item_counts { status count } item_attempts
        step_run {
          id status waiting_kind wait_reason outcome outcome_label failure_reason
          attempt page_index map_total map_settled created_at updated_at deadline_at wake_at
        }
      }
      edges { source outcome target taken }
    }
  }
}"""


@pytest.fixture
def schema():
    """Compose the addon's real console resource surface."""
    return addon_schema(workflow_schema.schemas, "console")


def read_graph(schema, run, actor):
    """Read only the graph summary, never execution payloads."""
    return result_data(execute_schema(schema, GRAPH, {"id": run.sqid}, user=actor))["workflowrun_by_pk"]


def test_fork_join_graph_keeps_pinned_labels_ports_and_taken_edges(schema, execution, register_step):
    actor, _ = execution

    class BranchRoute(Route):
        outcomes = {"done": "Done", "left": "Frozen branch", "right": "Right"}

    register_step(BranchRoute)
    document = {"nodes": {
        "entry": {"step": "route", "label": "Frozen entry", "config": {"outcome": "left"},
                  "outcome_labels": {"left": "Frozen branch"},
                  "next": {"left": ["first", "second"], "right": "unused"}},
        "first": {"step": "echo", "next": {"done": "finish"}},
        "second": {"step": "echo", "next": {"done": "finish"}},
        "unused": {"step": "echo"},
        "finish": {"step": "echo", "join": "all", "input": {"value": {"value": 9}}},
    }, "results": [{"from": "finish"}]}
    workflow = load_workflow(document, actor=actor)
    run = start_run(workflow, actor=actor)
    initial = read_graph(schema, run, actor)["graph"]
    nodes = {node["key"]: node for node in initial["nodes"]}
    assert nodes["entry"]["step_run"]["status"] == "READY"
    assert all(node["step_run"] is None for key, node in nodes.items() if key != "entry")
    assert not any(edge["taken"] for edge in initial["edges"])
    changed = {**document, "nodes": {**document["nodes"], "entry": {**document["nodes"]["entry"],
                                                                 "label": "New entry"}}}

    class CurrentRoute(BranchRoute):
        outcomes = {"done": "Done", "left": "Current branch", "right": "Right"}

    register_step(CurrentRoute)
    saved = Workflow.objects.save_draft(workflow, draft=changed, expected_revision=workflow.draft_revision, actor=actor)
    Workflow.objects.publish(workflow, expected_revision=saved.revision, actor=actor)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="entry").pk)
    projected = read_graph(schema, run, actor)["graph"]
    entry = next(node for node in projected["nodes"] if node["key"] == "entry")
    assert entry["label"] == "Frozen entry"
    assert {port["id"]: port["label"] for port in entry["outcomes"]}["left"] == "Frozen branch"
    assert entry["step_run"]["outcome_label"] == "Frozen branch"
    assert {(edge["source"], edge["target"]) for edge in projected["edges"] if edge["taken"]} == {
        ("entry", "first"), ("entry", "second"),
    }
    assert "input" not in entry["step_run"] and "output" not in entry["step_run"] and "state" not in entry["step_run"]
    run_until(run)
    assert sum(edge["taken"] for edge in read_graph(schema, run, actor)["graph"]["edges"]) == 4


def test_run_reader_gets_graph_without_workflow_or_version_read(schema, execution):
    actor, _ = execution
    reader, outsider = create_user("graph-reader"), create_user("graph-outsider")
    workflow = load_workflow({"nodes": {"entry": {"step": "reject", "label": "Retained failure"}}}, actor=actor)
    run = start_run(workflow, actor=actor)
    run.with_actor(actor).grant_record_access("reader", reader)
    run_until(run)
    visible = read_graph(schema, run, reader)
    assert visible["version"] is None
    node = visible["graph"]["nodes"][0]
    assert node["label"] == "Retained failure"
    assert node["step_run"]["failure_reason"] == "Rejected by the fixture."
    assert system_queryset(StepRun).get(run=run).failure_reason == run.failure_reason
    assert read_graph(schema, run, outsider) is None
    assert result_data(execute_schema(schema, "{ workflow { id } workflowversion { id } }", user=reader)) == {
        "workflow": [], "workflowversion": [],
    }


def test_map_graph_counts_all_items_and_attempts_at_constant_query_cost(schema, execution, settings):
    actor, _ = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1
    workflow = load_workflow({"nodes": {
        "items": {"step": "map", "body": {"step": "echo"},
                  "input": {"items": {"from": "input", "path": ["items"]}}},
    }}, actor=actor)
    counts = []
    for size in (1, 12):
        run = start_run(workflow, actor=actor, input={"items": [{"value": index} for index in range(size)]})
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            nodes = read_graph(schema, run, actor)["graph"]["nodes"]
        counts.append(len(queries))
        assert len(nodes) == 1
        node = nodes[0]
        assert node["body_key"] == "items.body"
        assert node["item_counts"] == [{"status": "SUCCEEDED", "count": size}]
        assert node["item_attempts"] == size
        assert node["step_run"]["map_total"] == node["step_run"]["map_settled"] == size
    assert counts[0] == counts[1]


def test_map_graph_summarizes_partial_progress_without_a_page_sample(schema, execution, settings):
    actor, _ = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1
    workflow = load_workflow({"nodes": {
        "items": {"step": "map", "body": {"step": "echo"},
                  "input": {"items": {"from": "input", "path": ["items"]}}},
    }}, actor=actor)
    run = start_run(workflow, actor=actor, input={"items": [{"value": 1}, {"value": 2}]})
    runner.execute(system_queryset(StepRun).get(run=run, node_key="items").pk)
    runner.execute(system_queryset(StepRun).get(run=run, node_key="items.body", map_index=0).pk)
    node = read_graph(schema, run, actor)["graph"]["nodes"][0]
    assert node["step_run"]["status"] == "WAITING"
    assert (node["step_run"]["map_total"], node["step_run"]["map_settled"]) == (2, 1)
    assert node["item_counts"] == [{"status": "READY", "count": 1}, {"status": "SUCCEEDED", "count": 1}]
    assert node["item_attempts"] == 1
