"""Run-pinned graph reads, bounded map aggregates and reader authorization."""

import re

import pytest
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel

from angee.iam.testing.graphql import measure_graphql_document
from angee.iam.testing.queries import is_permission_audit_insert
from angee.workflows import schema as workflow_schema
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import decide, deliver_decision_wakes, load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, Workflow
from tests.conftest import addon_schema, create_user, execute_schema, result_data, vault_for
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review import review as review
from tests.test_workflows_review import start_review
from tests.workflow_steps import Echo, Route

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]

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


def test_run_list_labels_failures_and_actions_are_flat_at_one_five_ten_twenty(schema, execution):
    """One page projects workflow labels, retained errors, and cancellation policy."""

    actor, _ = execution
    failed_workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="budget-failed", actor=actor)
    finished_workflow = load_workflow({"nodes": {"entry": {"step": "echo"}}}, key="budget-finished", actor=actor)
    for index in range(20):
        run = start_run(finished_workflow if index % 2 == 0 else failed_workflow, actor=actor)
        run_until(run)
    document = """query RunList($limit: Int!) {
        workflowrun(limit: $limit, order_by: [{created_at: asc}]) {
            id display_name failure_reason can_cancel
        }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=actor)
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["workflowrun"]) == size
        assert all(row["display_name"] for row in read.data["workflowrun"])
        assert [row["failure_reason"] for row in read.data["workflowrun"]] == [
            None if index % 2 == 0 else "Rejected by the fixture." for index in range(size)
        ]
        assert read.statement_count == 10
        read.assert_no_audit_writes()
        read.assert_bytes(total=80_000, largest=25_000)
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


def test_nested_run_cancel_does_not_prepare_outer_run_page(schema, execution):
    """A cancel field selected on related runs does not prepare the root run page."""

    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="deep-cancel-budget", actor=actor)
    for _ in range(20):
        run_until(start_run(workflow, actor=actor))
    document = """query DeepCancel($limit: Int!) {
        workflowrun(limit: $limit, order_by: [{created_at: asc}]) {
            id step_runs { id run { id can_cancel } }
        }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=actor)
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["workflowrun"]) == size
        assert all(len(run["step_runs"]) == 1 and run["step_runs"][0]["run"] is not None
                   for run in read.data["workflowrun"])
        assert read.statement_count == 13, (size, read.statement_count)
        read.assert_no_audit_writes()
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


def test_step_run_list_retry_projection_budget(schema, execution):
    """A generic step list must not repeat policy and graph reads per step."""

    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="retry-budget", actor=actor)
    for _ in range(20):
        run_until(start_run(workflow, actor=actor))
    document = """query RetryList($limit: Int!) {
        steprun(limit: $limit, order_by: [{created_at: asc}]) { id can_retry }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=actor)
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["steprun"]) == size
        assert read.statement_count == 9
        read.assert_no_audit_writes()
        read.assert_bytes(total=37_000, largest=21_000)
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


def test_empty_step_page_does_not_audit_effect_marker(schema, execution):
    """A projected effect marker adds no bypass audit for an empty outsider page."""

    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="empty-hold-budget", actor=actor)
    run_until(start_run(workflow, actor=actor))
    outsider = create_user("empty-hold-outsider")
    document = """query EmptySteps($limit: Int!) {
        steprun(limit: $limit) { id hold requires_duplicate_acknowledgement map_total map_settled }
    }"""
    for size in (1, 5, 10, 20):
        read = measure_graphql_document(schema, document, {"limit": size}, actor=outsider)
        assert read.data["steprun"] == []
        assert read.statement_count == 7
        read.assert_no_audit_writes()


def test_nested_step_run_retry_is_batched(schema, execution):
    """A run page prepares nested retry facts once for all selected steps."""

    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="nested-retry-budget", actor=actor)
    for _ in range(20):
        run_until(start_run(workflow, actor=actor))
    document = """query NestedRetry($limit: Int!) {
        workflowrun(limit: $limit, order_by: [{created_at: asc}]) {
            id step_runs { id can_retry }
        }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=actor)
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["workflowrun"]) == size
        assert all(len(run["step_runs"]) == 1 and run["step_runs"][0]["can_retry"]
                   for run in read.data["workflowrun"])
        assert read.statement_count == 13, (size, read.statement_count)
        read.assert_no_audit_writes()
        read.assert_bytes(total=100_000, largest=30_000)
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


def test_nested_run_cancel_is_batched(schema, execution):
    """A step page prepares the related run's cancel facts once."""

    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "reject"}}}, key="nested-cancel-budget", actor=actor)
    for _ in range(20):
        run_until(start_run(workflow, actor=actor))
    document = """query NestedCancel($limit: Int!) {
        steprun(limit: $limit, order_by: [{created_at: asc}]) {
            id run { id can_cancel }
        }
    }"""
    reads = {
        size: measure_graphql_document(schema, document, {"limit": size}, actor=actor)
        for size in (1, 5, 10, 20)
    }
    for size, read in reads.items():
        assert len(read.data["steprun"]) == size
        assert all(row["run"] is not None and row["run"]["can_cancel"] for row in read.data["steprun"])
        assert read.statement_count == 9, (size, read.statement_count)
        read.assert_no_audit_writes()
    for smaller, larger in ((1, 5), (5, 10), (10, 20)):
        reads[larger].assert_flat_from(reads[smaller])


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
    runner.execute(StepRun.system_queryset().get(run=run, node_key="entry").pk)
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
    with CaptureQueriesContext(connection) as queries:
        visible = read_graph(schema, run, reader)
    assert sum(is_permission_audit_insert(query["sql"], connection) for query in queries) == 0
    # One admitted run batches its pinned execution policy without exposing version.
    assert len(queries) == 21
    assert sum(len(query["sql"].encode("utf-8")) for query in queries) <= 180_000
    assert visible["version"] is None
    node = visible["graph"]["nodes"][0]
    assert node["label"] == "Retained failure"
    assert node["step_run"]["failure_reason"] == "Rejected by the fixture."
    with CaptureQueriesContext(connection) as outcome_queries:
        outcome = result_data(execute_schema(
            schema,
            "query($id:String!) { workflowrun_by_pk(id:$id) { outcome_label } }",
            {"id": run.sqid}, user=reader,
        ))["workflowrun_by_pk"]["outcome_label"]
    assert outcome
    assert sum(is_permission_audit_insert(query["sql"], connection) for query in outcome_queries) == 0
    # The selected run's pinned outcome vocabulary is one page-level policy read.
    assert len(outcome_queries) == 4
    assert sum(len(query["sql"].encode("utf-8")) for query in outcome_queries) <= 50_000
    with CaptureQueriesContext(connection) as input_queries:
        result_data(execute_schema(
            schema,
            "query($id:String!) { workflowrun_by_pk(id:$id) { input } }",
            {"id": run.sqid}, user=reader,
        ))
    assert sum(is_permission_audit_insert(query["sql"], connection) for query in input_queries) == 0
    # Input redaction shares the admitted run's pinned policy page read.
    assert len(input_queries) == 4
    assert sum(len(query["sql"].encode("utf-8")) for query in input_queries) <= 50_000
    assert StepRun.system_queryset().get(run=run).failure_reason == run.get_failure_reason()
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
            assert node["label"] == StepRun.system_queryset().get(run=run, node_key=node["key"]).node_label
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


def test_graph_requires_a_reader_and_uses_actor_scoped_step_resources(schema, execution):
    actor, _ = execution
    run = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor)
    with pytest.raises(PermissionDenied, match="requires a reader"):
        run.graph(None)
    document = GRAPH.replace("id status waiting_kind", "id input attempts { id } status waiting_kind")
    result = execute_schema(schema, document, {"id": run.sqid}, user=actor)
    assert result.errors is None
    assert result.data["workflowrun_by_pk"]["graph"]["nodes"][0]["step_run"]["attempts"] == []
    hidden = execute_schema(schema, document, {"id": run.sqid}, user=create_user("graph-outsider"))
    assert hidden.errors is None and hidden.data["workflowrun_by_pk"] is None


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
    assert StepRun.system_queryset().filter(run=run, status="failed").count() == 3
    assert run.outcome == "error"
    run.output = {"error": "Run failure"}
    assert run.failure_reason == "Run failure"
    run.output = {}
    assert run.get_failure_reason() == "Second failure"
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
    runner.execute(StepRun.system_queryset().get(run=run, node_key="items").pk)
    runner.execute(StepRun.system_queryset().get(run=run, node_key="items.body", map_index=0).pk)
    node = read_graph(schema, run, actor)["graph"]["nodes"][0]
    assert node["step_run"]["status"] == "WAITING"
    assert (node["step_run"]["map_total"], node["step_run"]["map_settled"]) == (2, 1)
    assert node["item_counts"] == [{"status": "READY", "count": 1}, {"status": "SUCCEEDED", "count": 1}]
    assert node["item_attempts"] == 1
    detail = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph { nodes { step_run {
        map_steps { id map_index can_retry requires_duplicate_acknowledgement }
      } } } }
    }""", {"id": run.sqid}, user=actor))["workflowrun_by_pk"]["graph"]["nodes"][0]["step_run"]
    assert [item["map_index"] for item in detail["map_steps"]] == [0, 1]
    assert all(not item["can_retry"] and not item["requires_duplicate_acknowledgement"] for item in detail["map_steps"])


def test_drill_in_step_permission_queries_are_bounded(schema, execution):
    admin, _ = execution
    starter = create_user("drill-in-starter")
    record = vault_for(starter)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records, run_limit: 1) { records { runs {
        id can_cancel graph { nodes { key step_run {
          id status hold outcome_label failure_reason wait_reason
          can_retry requires_duplicate_acknowledgement
          awaited_run { id } child_runs { id } records { id }
          decision { id is_open }
          map_steps { id can_retry requires_duplicate_acknowledgement }
        } } }
      } } }
    }"""
    counts = []
    last_run = None
    for count in (2, 6):
        document = {"nodes": {
            f"node_{index}": {
                "step": "reject" if index == count - 1 else "echo",
                **({"next": {"done": f"node_{index + 1}"}} if index < count - 1 else {}),
            }
            for index in range(count)
        }}
        workflow = load_workflow(document, key=f"drill-in-{count}", actor=admin)
        workflow.with_actor(admin).grant_record_access("starter", starter)
        run = start_run(workflow, actor=starter, subject=record)
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, query, {"records": [
                {"model": record._meta.label, "id": record.sqid},
            ]}, user=starter))["record_timeline"]["records"][0]["runs"][0]
        counts.append(len(queries))
        assert [node["step_run"]["can_retry"] for node in data["graph"]["nodes"]] == [
            *([False] * (count - 1)), True,
        ]
        assert all(not node["step_run"]["requires_duplicate_acknowledgement"] for node in data["graph"]["nodes"])
        last_run = run
    assert counts[0] == counts[1]
    assert last_run is not None
    reader = create_user("drill-in-reader")
    last_run.with_actor(starter).grant_record_access("reader", reader)
    denied = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph { nodes { step_run { can_retry } } } }
    }""", {"id": last_run.sqid}, user=reader))["workflowrun_by_pk"]
    assert [node["step_run"]["can_retry"] for node in denied["graph"]["nodes"]] == [False] * 6
    operator = create_user("drill-in-operator")
    last_run.with_actor(starter).grant_record_access("operator", operator)
    assert last_run.get_can_cancel(operator)
    graph = last_run.graph(operator, step_fields=frozenset({"can_retry"}))
    failed = graph.nodes[-1].step_run
    assert failed is not None and failed.can_retry(operator)
    last_run.with_actor(starter).revoke_record_access("operator", operator)
    assert not last_run.get_can_cancel(operator)
    unrelated = StepRun.system_queryset().get(pk=failed.pk)
    unrelated.run = last_run
    assert not unrelated.get_can_retry(operator)


def test_drill_in_record_reference_queries_are_bounded(schema, execution, register_step):
    admin, _ = execution
    starter = create_user("drill-record-starter")
    subject = vault_for(starter)

    class RecordEcho(Echo):
        key = "drill_record_echo"

        def run(self, ctx):
            ctx.record(ctx.run)
            if self.include_subject:
                ctx.record(ctx.subject)
            return ctx.done(ctx.input)

    register_step(RecordEcho)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records, run_limit: 1) { records { runs {
        id status can_cancel graph { nodes { key plan step_run {
          id status hold outcome_label failure_reason wait_reason
          can_retry requires_duplicate_acknowledgement
          awaited_run { id } child_runs { id }
          records { id label operation record_model record_id }
          decision { id permissions records { id record_model record_id }
            requester { display_name } assignees { display_name } answered_by { display_name } }
          map_steps { id can_retry requires_duplicate_acknowledgement
            records { id label operation record_model record_id }
            decision { id permissions records { id record_model record_id } } }
        } } }
      } } }
    }"""
    counts = []
    for count, record_count in ((2, 1), (2, 2), (6, 2)):
        RecordEcho.include_subject = record_count == 2
        workflow = load_workflow({"nodes": {
            f"node_{index}": {
                "step": RecordEcho.key,
                **({"next": {"done": f"node_{index + 1}"}} if index < count - 1 else {}),
            }
            for index in range(count)
        }}, key=f"drill-record-{count}-{record_count}", actor=admin)
        workflow.with_actor(admin).grant_record_access("starter", starter)
        run = start_run(workflow, actor=starter, subject=subject)
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, query, {"records": [
                {"model": subject._meta.label, "id": subject.sqid},
            ]}, user=starter))["record_timeline"]["records"][0]["runs"][0]
        counts.append(len(queries))
        records = [node["step_run"]["records"] for node in data["graph"]["nodes"]]
        assert [len(rows) for rows in records] == [record_count] * count
        assert all(all(link["record_model"] and link["record_id"] for link in rows) for rows in records)
        assert [node["step_run"]["can_retry"] for node in data["graph"]["nodes"]] == [False] * count
    assert counts[0] == counts[1] == counts[2], counts


def test_record_reuses_the_step_load_admission(execution, register_step):
    actor, _ = execution
    subject = vault_for(actor)
    statements = []

    class AdmittedRecord(Echo):
        key = "admitted_record"

        def run(self, ctx):
            row = ctx.load(type(subject), subject.sqid)
            with CaptureQueriesContext(connection) as queries:
                ctx.record(row)
            statements.append(len(queries))
            return ctx.done(ctx.input)

    register_step(AdmittedRecord)
    workflow = load_workflow({"nodes": {"entry": {"step": AdmittedRecord.key}}}, actor=actor)
    run_until(start_run(workflow, actor=actor, subject=subject))
    print(f"admitted record statements: {statements}")
    assert statements == [0]


def test_graph_prefetches_real_decision_and_child_relations(schema, review, child_graph):
    actor, _, _, _ = review
    decision_run, decision_step = start_review(review)
    decision = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph { nodes { step_run { decision {
        id permissions records { id record_model record_id }
        requester { display_name } assignees { display_name } answered_by { display_name }
      } } } } }
    }""", {"id": decision_run.sqid}, user=actor))["workflowrun_by_pk"]["graph"]["nodes"][0]["step_run"]["decision"]
    assert decision["id"] and decision["records"]
    assert decision["records"][0]["record_model"] and decision["records"][0]["record_id"]
    assert decision_step.decision_id

    child_actor, _, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    graph = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph { nodes { step_run {
        id awaited_run { id } child_runs { id }
      } } } }
    }""", {"id": parent.sqid}, user=child_actor))["workflowrun_by_pk"]["graph"]["nodes"]
    assert any(node["step_run"]["awaited_run"] for node in graph if node["step_run"])
    assert any(node["step_run"]["child_runs"] for node in graph if node["step_run"])
    assert admitted


def test_drill_in_map_record_reference_queries_are_bounded(schema, execution, register_step, settings):
    actor, _ = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1
    subject = vault_for(actor)

    class RecordItem(Step[None, None, None]):
        key = "drill_record_item"

        def run(self, ctx):
            ctx.record(ctx.run)
            ctx.record(ctx.subject)
            return ctx.done(ctx.input)

    register_step(RecordItem)
    query = """query($id: String!) { workflowrun_by_pk(id: $id) {
      graph { nodes { step_run { map_steps {
        id records { id record_model record_id }
      } } } }
    } }"""
    counts = []
    for size in (2, 6):
        workflow = load_workflow({"nodes": {
            "items": {"step": "map", "body": {"step": RecordItem.key},
                      "input": {"items": {"from": "input", "path": ["items"]}}},
        }}, key=f"drill-map-record-{size}", actor=actor)
        run = start_run(workflow, actor=actor, subject=subject, input={"items": [{"value": i} for i in range(size)]})
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            nodes = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))[
                "workflowrun_by_pk"
            ]["graph"]["nodes"]
        counts.append(len(queries))
        rows = nodes[0]["step_run"]["map_steps"]
        assert [len(item["records"]) for item in rows] == [2] * size
        assert all(link["record_model"] and link["record_id"] for item in rows for link in item["records"])
    assert counts[0] == counts[1], counts


def test_drill_in_decision_card_queries_are_bounded(schema, review):
    actor, people, sent, question = review
    query = """query($id: String!) { workflowrun_by_pk(id: $id) {
      graph { nodes { step_run { records { id record_model record_id } decision {
        id permissions records { id record_model record_id }
        requester { display_name } assignees { display_name } answered_by { display_name }
      } } } }
    } }"""
    counts = []
    for size in (2, 6):
        workflow = load_workflow({"nodes": {
            f"node_{index}": {
                "step": question.key,
                **({"next": {"approved": f"node_{index + 1}"}} if index < size - 1 else {}),
            }
            for index in range(size)
        }}, key=f"drill-decision-{size}", actor=actor)
        run = start_run(workflow, actor=actor, subject=question.review_subject)
        for index in range(size):
            run_until(run)
            step = StepRun.system_queryset().get(run=run, node_key=f"node_{index}")
            decision = step.decision
            decide(decision, actor=people[0], chosen=["approve"])
            assert deliver_decision_wakes(sent, decision.pk) == [1]
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            nodes = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))[
                "workflowrun_by_pk"
            ]["graph"]["nodes"]
        counts.append(len(queries))
        cards = [node["step_run"]["decision"] for node in nodes]
        assert len(cards) == size and all(card and card["records"] for card in cards)
        assert all(node["step_run"]["records"] for node in nodes)
        assert all(card["assignees"] and card["answered_by"] for card in cards)
        assert all(card["records"][0]["record_model"] and card["records"][0]["record_id"] for card in cards)
        assert all(
            link["record_model"] and link["record_id"]
            for node in nodes for link in node["step_run"]["records"]
        )
    assert counts[0] == counts[1], counts


def test_drill_in_map_decision_card_prefetch_is_populated(schema, review, settings):
    actor, _, _, question = review
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 6
    query = """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph { nodes { step_run { map_steps {
        id records { id record_model record_id } decision {
          id permissions records { id record_model record_id }
          assignees { display_name }
        }
      } } } } }
    }"""
    counts = []
    for size in (2, 6):
        workflow = load_workflow({"nodes": {
            "items": {"step": "map", "body": {"step": question.key},
                      "input": {"items": {"from": "input", "path": ["items"]}}},
        }}, key=f"drill-map-decision-{size}", actor=actor)
        run = start_run(workflow, actor=actor, subject=question.review_subject, input={"items": [{}] * size})
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            nodes = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))[
                "workflowrun_by_pk"
            ]["graph"]["nodes"]
        counts.append(len(queries))
        items = nodes[0]["step_run"]["map_steps"]
        assert len(items) == size
        assert all(item["records"] for item in items)
        assert all(item["decision"] and item["decision"]["records"] for item in items)
        assert all(item["decision"]["assignees"] for item in items)
    assert counts[0] == counts[1], counts
