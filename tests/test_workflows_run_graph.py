"""Run-pinned graph reads, bounded map aggregates and reader authorization."""

import re

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel

from angee.base.scoping import system_queryset
from angee.workflows import schema as workflow_schema
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.workflow_steps import Echo, Route

pytestmark = pytest.mark.usefixtures("workflow_step_classes")

GRAPH = """query($id: String!) {
  workflowrun_by_pk(id: $id) {
    id version { id }
    graph {
      nodes {
        key label step_label rank body_key outcomes { outcome label }
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
    assert {port["outcome"]: port["label"] for port in entry["outcomes"]}["left"] == "Frozen branch"
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
    counts = []
    for node_count, size in ((1, 1), (5, 1), (5, 12)):
        workflow = load_workflow({"nodes": {
            f"items_{index}": {"step": "map", "body": {"step": "echo"},
                               "input": {"items": {"from": "input", "path": ["items"]}},
                               **({"next": {"done": f"items_{index + 1}"}} if index + 1 < node_count else {})}
            for index in range(node_count)
        }}, key=f"maps_{node_count}_{size}", actor=actor)
        run = start_run(workflow, actor=actor, input={"items": [{"value": index} for index in range(size)]})
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            nodes = read_graph(schema, run, actor)["graph"]["nodes"]
        counts.append(len(queries))
        assert len(nodes) == node_count
        for node in nodes:
            assert node["label"] == f"Items {node['key'].rsplit('_', 1)[1]}"
            assert node["label"] == system_queryset(StepRun).get(run=run, node_key=node["key"]).node_label
            assert node["body_key"] == node["key"] + ".body"
            assert node["item_counts"] == [{"status": "SUCCEEDED", "count": size}]
            assert node["item_attempts"] == size
            assert node["step_run"]["map_total"] == node["step_run"]["map_settled"] == size
        table = StepRun._meta.db_table
        payload_column = rf'"{table}"\."(?:input|output|state)"'
        for query in queries.captured_queries:
            assert not re.search(rf'(?:SELECT |,\s*){payload_column}(?:\s*,|\s+FROM)', query["sql"])
        summary = run.graph(actor)
        assert all({"input", "output", "state"} <= node.step_run.get_deferred_fields() for node in summary.nodes)
    assert len(set(counts)) == 1


def test_graph_requires_a_reader_and_cannot_traverse_step_payloads(schema, execution):
    actor, _ = execution
    run = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor)
    with pytest.raises(PermissionDenied, match="requires a reader"):
        run.graph(None)
    document = GRAPH.replace("id status waiting_kind", "id input attempts { id } status waiting_kind")
    result = execute_schema(schema, document, {"id": run.sqid}, user=actor)
    assert result.errors
    for field in ("input", "attempts"):
        assert any(f"Cannot query field '{field}' on type 'WorkflowRunGraphStepRun'" in error.message
                   for error in result.errors)


def test_graph_survives_removing_a_published_step_implementation(schema, execution, settings):
    """A parked execution remains inspectable with its published reader vocabulary."""
    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "echo", "label": "Retained step"}}}, actor=actor)
    run = start_run(workflow, actor=actor)
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        key: value for key, value in settings.ANGEE_WORKFLOW_STEP_CLASSES.items() if key != "echo"
    }
    node = read_graph(schema, run, actor)["graph"]["nodes"][0]
    assert node["label"] == "Retained step" and node["step_label"] == "echo"
    assert node["step_run"]["status"] == "READY"


def test_failure_routed_error_edge_is_taken(schema, execution):
    actor, _ = execution
    workflow = load_workflow({"nodes": {
        "entry": {"step": "reject", "next": {"error": "recover"}},
        "recover": {"step": "echo", "input": {"value": {"value": 1}}},
    }}, actor=actor)
    run = start_run(workflow, actor=actor)
    run_until(run)
    graph = read_graph(schema, run, actor)["graph"]
    assert graph["edges"] == [{"source": "entry", "outcome": "error", "target": "recover", "taken": True}]


def test_run_failure_reason_prefers_output_then_first_retained_failed_error(execution, register_step):
    """Reader diagnostics have one deterministic precedence and require an error outcome."""
    actor, _ = execution

    class Failing(Echo):
        def run(self, ctx):
            return ctx.fail({"entry": "", "second": "Second failure", "third": "Third failure"}[ctx.step_run.node_key])

    register_step(Failing)
    workflow = load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"error": "second"}},
        "second": {"step": "echo", "next": {"error": "third"}},
        "third": {"step": "echo"},
    }}, actor=actor)
    run = start_run(workflow, actor=actor)
    run_until(run)
    assert system_queryset(StepRun).filter(run=run, status="failed").count() == 3
    assert run.outcome == "error"
    run.output = {"error": "Run failure"}
    assert run.failure_reason == "Run failure"
    run.output = {}
    assert run.failure_reason == "Second failure"
    run.outcome = "done"
    assert run.failure_reason is None


def test_non_map_input_items_are_not_interpreted_as_admitted_arrays(schema, execution, register_step):
    """Bulk map totals remain safe for ordinary product input shapes."""
    actor, _ = execution

    class Input(BaseModel):
        items: str

    class TextItems(Step[Input, Input, None]):
        key = "text_items"

    register_step(TextItems)
    workflow = load_workflow({"nodes": {
        "entry": {"step": "text_items", "input": {"items": {"value": "plain text"}}},
    }}, actor=actor)
    run = start_run(workflow, actor=actor)
    assert read_graph(schema, run, actor)["graph"]["nodes"][0]["step_run"]["map_total"] == 0


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
