"""Run-pinned graph reads, bounded map aggregates and reader authorization."""

import re

import pytest
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.db import connection
from django.db.models.functions import Now
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel

from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows import schema as workflow_schema
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import decide, deliver_decision_wakes, load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import addon_schema, create_user, vault_for
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review import review as review
from tests.test_workflows_review import start_review
from tests.workflow_steps import Echo, Route

pytestmark = pytest.mark.usefixtures("workflow_step_classes")

GRAPH = """query($id: String!) {
  workflowrun_by_pk(id: $id) {
    id version { id }
    graph_step_runs {
      id status waiting_kind wait_reason outcome outcome_label failure_reason
      attempt page_index map_total map_settled created_at updated_at deadline_at wake_at
    }
    graph {
      nodes {
        key label step_label rank body_key outcomes { outcome label }
        item_counts { status count } item_attempts step_run_id
      }
      edges { source outcome target taken }
    }
  }
}"""


def graph_nodes(run):
    """A read run's graph nodes, each joined with the step row it names in ``graph_step_runs``."""
    steps = {step["id"]: step for step in run["graph_step_runs"]}
    return [{**node, "step_run": steps.get(node["step_run_id"])} for node in run["graph"]["nodes"]]


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
    initial = read_graph(schema, run, actor)
    nodes = {node["key"]: node for node in graph_nodes(initial)}
    assert nodes["entry"]["step_run"]["status"] == "READY"
    assert all(node["step_run"] is None for key, node in nodes.items() if key != "entry")
    assert not any(edge["taken"] for edge in initial["graph"]["edges"])
    changed = {**document, "nodes": {**document["nodes"], "entry": {**document["nodes"]["entry"],
                                                                 "label": "New entry"}}}

    class CurrentRoute(BranchRoute):
        outcomes = {"done": "Done", "left": "Current branch", "right": "Right"}

    register_step(CurrentRoute)
    saved = Workflow.objects.save_draft(workflow, draft=changed, expected_revision=workflow.draft_revision, actor=actor)
    Workflow.objects.publish(workflow, expected_revision=saved.revision, actor=actor)
    runner.execute(StepRun.system_queryset().get(run=run, node_key="entry").pk)
    projected = read_graph(schema, run, actor)
    entry = next(node for node in graph_nodes(projected) if node["key"] == "entry")
    assert entry["label"] == "Frozen entry"
    assert {port["outcome"]: port["label"] for port in entry["outcomes"]}["left"] == "Frozen branch"
    assert entry["step_run"]["outcome_label"] == "Frozen branch"
    assert {(edge["source"], edge["target"]) for edge in projected["graph"]["edges"] if edge["taken"]} == {
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
    node = graph_nodes(visible)[0]
    assert node["label"] == "Retained failure"
    assert node["step_run"]["failure_reason"] == "Rejected by the fixture."
    assert StepRun.system_queryset().get(run=run).failure_reason == run.failure_reason
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
            nodes = graph_nodes(read_graph(schema, run, actor))
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
        assert summary is not None
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
    assert result.data["workflowrun_by_pk"]["graph_step_runs"][0]["attempts"] == []
    hidden = execute_schema(schema, document, {"id": run.sqid}, user=create_user("graph-outsider"))
    assert hidden.errors is None and hidden.data["workflowrun_by_pk"] is None


def test_run_list_graphs_leave_out_only_a_run_the_reader_lost(execution):
    """A run lost between the list read and the graph read gets no graph; the others still do."""
    actor, _ = execution
    reader = create_user("graph-list-reader")
    workflow = load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor)
    kept, lost = start_run(workflow, actor=actor), start_run(workflow, actor=actor)
    for run in (kept, lost):
        run.with_actor(actor).grant_record_access("reader", reader)
    listed = {run.pk: run for run in WorkflowRun.objects.with_actor(reader).filter(pk__in=[kept.pk, lost.pk])}
    assert set(listed) == {kept.pk, lost.pk}
    lost.with_actor(actor).revoke_record_access("reader", reader)
    graphs = WorkflowRun.graphs_for(list(listed.values()), reader)
    assert set(graphs) == {kept.pk}
    assert [node.node.key for node in graphs[kept.pk].nodes] == ["entry"]
    assert listed[lost.pk].graph(reader) is None


def test_graph_survives_removing_a_published_step_implementation(schema, execution, settings):
    """A parked execution remains inspectable with its published reader vocabulary."""
    actor, _ = execution
    workflow = load_workflow({"nodes": {"entry": {"step": "echo", "label": "Retained step"}}}, actor=actor)
    run = start_run(workflow, actor=actor)
    without_step(settings, "echo")
    node = graph_nodes(read_graph(schema, run, actor))[0]
    assert node["label"] == "Retained step" and node["step_label"] == "echo"
    assert node["step_run"]["status"] == "READY"
    # The batched effect marker answers before the missing implementation is consulted.
    detail = execute_schema(schema, """query($id: String!) { workflowrun_by_pk(id: $id) {
      graph_step_runs { status requires_duplicate_acknowledgement }
    } }""", {"id": run.sqid}, user=actor)
    assert detail.errors is None
    assert detail.data["workflowrun_by_pk"]["graph_step_runs"][0] == {
        "status": "READY", "requires_duplicate_acknowledgement": False,
    }


def without_step(settings, key):
    """Unregister one step key, as removing its addon would."""
    settings.ANGEE_WORKFLOW_STEP_CLASSES = {
        name: value for name, value in settings.ANGEE_WORKFLOW_STEP_CLASSES.items() if name != key
    }


def test_removed_step_answers_the_fail_closed_effect_default(schema, execution, register_step, settings):
    """Once an effect started, a step whose implementation is gone requires acknowledgement."""
    actor, _ = execution

    class IdempotentEcho(Echo):
        key = "idempotent_echo"
        effect_idempotent = True

    register_step(IdempotentEcho)
    run = start_run(load_workflow({"nodes": {"entry": {"step": IdempotentEcho.key}}}, actor=actor), actor=actor)
    step = StepRun.system_queryset().get(run=run, node_key="entry")
    assert runner.execute(step.pk)
    StepAttempt.system_queryset().filter(step_run=step).update(effect_started_at=Now())
    query = """query($id: String!) { workflowrun_by_pk(id: $id) {
      graph_step_runs { requires_duplicate_acknowledgement }
    } }"""

    def acknowledgement():
        result = execute_schema(schema, query, {"id": run.sqid}, user=actor)
        assert result.errors is None
        return result.data["workflowrun_by_pk"]["graph_step_runs"][0]["requires_duplicate_acknowledgement"]

    assert acknowledgement() is False
    assert not StepRun.system_queryset().get(pk=step.pk).requires_duplicate_acknowledgement
    without_step(settings, IdempotentEcho.key)
    assert acknowledgement() is True
    assert StepRun.system_queryset().get(pk=step.pk).requires_duplicate_acknowledgement
    outsider = create_user("effect-marker-outsider")
    assert StepRun.system_queryset().get(pk=step.pk).with_actor(outsider).requires_duplicate_acknowledgement


def test_projected_effect_marker_answers_before_the_run_is_read(execution):
    """A step with no effect on its page answers from the projected marker alone."""
    actor, _ = execution
    run = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor)
    step = StepRun.system_queryset().annotate(
        _effect_started=StepRun.effect_started_expression(),
    ).get(run=run, node_key="entry")
    with CaptureQueriesContext(connection) as queries:
        assert step.requires_duplicate_acknowledgement is False
    assert queries.captured_queries == []


def test_removed_entry_step_leaves_the_run_input_unreadable(schema, execution, settings):
    """A reader cannot classify input references without the consuming step, so they stay hidden."""
    actor, _ = execution
    run = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor, input={"value": 3})
    query = "query($id: String!) { workflowrun_by_pk(id: $id) { input } }"
    readable = execute_schema(schema, query, {"id": run.sqid}, user=actor)
    assert readable.errors is None and readable.data["workflowrun_by_pk"]["input"] == {"value": 3}
    without_step(settings, "echo")
    hidden = execute_schema(schema, query, {"id": run.sqid}, user=actor)
    assert hidden.errors is None and hidden.data["workflowrun_by_pk"]["input"] is None
    definition = WorkflowRun.system_queryset().get(pk=run.pk).policy_version.definition
    assert definition.input_evidence(run.input) == ()
    assert definition.redacted_input(run.input, set()) is None
    with pytest.raises(ImproperlyConfigured):
        definition.validate_input(run.input)


def test_reader_input_sets_aside_only_consumers_an_unregistered_step_owns(execution, register_step, settings):
    """A broken registered contract still raises while an unrelated downstream step is unregistered."""
    actor, _ = execution

    class ReaderConsumer(Echo):
        key = "reader_consumer"

    register_step(ReaderConsumer)
    workflow = load_workflow({"nodes": {
        "entry": {"step": ReaderConsumer.key, "next": {"done": "later"}},
        "later": {"step": "echo"},
    }}, actor=actor)
    run = start_run(workflow, actor=actor, input={"value": 4})

    class BrokenConsumer(ReaderConsumer):
        @classmethod
        def input_schema(cls):
            raise ImproperlyConfigured("broken consumer contract")

    register_step(BrokenConsumer)
    without_step(settings, "echo")
    definition = WorkflowRun.system_queryset().get(pk=run.pk).policy_version.definition
    assert definition.unregistered_steps == {"later"}
    with pytest.raises(ImproperlyConfigured, match="broken consumer contract"):
        definition.redacted_input(run.input, set())


def test_run_document_resolves_with_null_input_when_its_entry_step_is_gone(schema, execution, settings):
    """The run record read keeps resolving; only the unreadable input projects null."""
    actor, _ = execution
    run = start_run(load_workflow({"nodes": {"entry": {"step": "echo"}}}, actor=actor), actor=actor, input={"value": 5})
    without_step(settings, "echo")
    result = execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { id status input output redacted_fields }
    }""", {"id": run.sqid}, user=actor)
    assert result.errors is None
    projected = result.data["workflowrun_by_pk"]
    assert projected["id"] == run.sqid and projected["input"] is None
    assert "input" not in projected["redacted_fields"]


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
    """Reader diagnostics have one deterministic precedence and require an error outcome.

    The failed steps are read elevated, whichever actor the run is bound to.
    """
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
    assert run.failure_reason == "Second failure"
    assert run.with_actor(create_user("failure-reason-outsider")).failure_reason == "Second failure"
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
    assert read_graph(schema, run, actor)["graph_step_runs"][0]["map_total"] == 0


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
    node = graph_nodes(read_graph(schema, run, actor))[0]
    assert node["step_run"]["status"] == "WAITING"
    assert (node["step_run"]["map_total"], node["step_run"]["map_settled"]) == (2, 1)
    assert node["item_counts"] == [{"status": "READY", "count": 1}, {"status": "SUCCEEDED", "count": 1}]
    assert node["item_attempts"] == 1
    detail = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph_step_runs {
        map_steps { id map_index can_retry requires_duplicate_acknowledgement }
      } }
    }""", {"id": run.sqid}, user=actor))["workflowrun_by_pk"]["graph_step_runs"][0]
    assert [item["map_index"] for item in detail["map_steps"]] == [0, 1]
    assert all(not item["can_retry"] and not item["requires_duplicate_acknowledgement"] for item in detail["map_steps"])


def chain(step, count, *, outcome="done"):
    """A linear graph of ``count`` nodes running one step."""
    return {"nodes": {
        f"node_{index}": {"step": step, **({"next": {outcome: f"node_{index + 1}"}} if index < count - 1 else {})}
        for index in range(count)
    }}


def test_drill_in_step_permission_queries_are_bounded(schema, execution):
    """One run write check and one effect read serve every node of a drill-in."""
    admin, _ = execution
    starter = create_user("drill-in-starter")
    record = vault_for(starter)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records, run_limit: 1) { records { runs {
        id can_cancel graph { nodes { key step_run_id } } graph_step_runs {
          id status hold outcome_label failure_reason wait_reason
          can_retry requires_duplicate_acknowledgement
          awaited_run { id } child_runs { id } records { id }
          decision { id }
          map_steps { id can_retry requires_duplicate_acknowledgement }
        }
      } } }
    }"""
    counts = []
    last_run = None
    for count in (2, 6):
        document = chain("echo", count)
        document["nodes"][f"node_{count - 1}"]["step"] = "reject"
        workflow = load_workflow(document, key=f"drill-in-{count}", actor=admin)
        workflow.with_actor(admin).grant_record_access("starter", starter)
        run = start_run(workflow, actor=starter, subject=record)
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, query, {"records": [
                {"model": record._meta.label, "id": record.sqid},
            ]}, user=starter))["record_timeline"]["records"][0]["runs"][0]
        counts.append(len(queries))
        assert [node["step_run"]["can_retry"] for node in graph_nodes(data)] == [*([False] * (count - 1)), True]
        assert not any(node["step_run"]["requires_duplicate_acknowledgement"] for node in graph_nodes(data))
        last_run = run
    assert counts[0] == counts[1], counts
    assert last_run is not None
    reader = create_user("drill-in-reader")
    last_run.with_actor(starter).grant_record_access("reader", reader)
    denied = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph_step_runs { can_retry } }
    }""", {"id": last_run.sqid}, user=reader))["workflowrun_by_pk"]
    assert [step["can_retry"] for step in denied["graph_step_runs"]] == [False] * 6
    operator = create_user("drill-in-operator")
    last_run.with_actor(starter).grant_record_access("operator", operator)
    assert last_run.can_cancel(operator)
    graph = last_run.graph(operator)
    assert graph is not None
    failed = graph.nodes[-1].step_run
    assert failed is not None and failed.can_retry(operator)
    last_run.with_actor(starter).revoke_record_access("operator", operator)
    # The graph read remembered only the graph, so the read row asks the write policy again.
    assert not last_run.can_cancel(operator)
    # The batch's answers belong to the run rows it read; a fresh read asks the policy again.
    fresh = WorkflowRun.system_queryset().get(pk=last_run.pk)
    assert not fresh.can_cancel(operator)
    unseeded = StepRun.system_queryset().get(pk=failed.pk)
    unseeded.run = fresh
    assert not unseeded.can_retry(operator)


def test_drill_in_record_reference_queries_are_bounded(schema, execution, register_step):
    """Step evidence carries its batched target-read answer for every node and item."""
    admin, _ = execution
    starter = create_user("drill-record-starter")
    subject = vault_for(starter)

    class RecordEcho(Echo):
        key = "drill_record_echo"
        include_subject = False

        def run(self, ctx):
            ctx.record(ctx.run)
            if self.include_subject:
                ctx.record(ctx.subject)
            return ctx.done(ctx.input)

    register_step(RecordEcho)
    query = """query($records: [TimelineRecordInput!]!) {
      record_timeline(records: $records, run_limit: 1) { records { runs {
        id status can_cancel graph { nodes { key plan step_run_id } } graph_step_runs {
          id status hold outcome_label failure_reason wait_reason
          can_retry requires_duplicate_acknowledgement
          awaited_run { id display_name } child_runs { id display_name }
          records { id label operation record_model record_id }
          decision { id }
          map_steps { id can_retry requires_duplicate_acknowledgement
            records { id label operation record_model record_id } decision { id } }
        }
      } } }
    }"""
    counts = []
    for count, record_count in ((2, 1), (2, 2), (6, 2)):
        RecordEcho.include_subject = record_count == 2
        workflow = load_workflow(chain(RecordEcho.key, count), key=f"drill-record-{count}-{record_count}", actor=admin)
        workflow.with_actor(admin).grant_record_access("starter", starter)
        run = start_run(workflow, actor=starter, subject=subject)
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            data = result_data(execute_schema(schema, query, {"records": [
                {"model": subject._meta.label, "id": subject.sqid},
            ]}, user=starter))["record_timeline"]["records"][0]["runs"][0]
        counts.append(len(queries))
        records = [node["step_run"]["records"] for node in graph_nodes(data)]
        assert [len(rows) for rows in records] == [record_count] * count
        assert all(link["record_model"] and link["record_id"] for rows in records for link in rows)
        assert [node["step_run"]["can_retry"] for node in graph_nodes(data)] == [False] * count
    assert counts[0] == counts[1] == counts[2], counts


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
      graph_step_runs { map_steps { id records { id record_model record_id } } }
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
            steps = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))[
                "workflowrun_by_pk"
            ]["graph_step_runs"]
        counts.append(len(queries))
        rows = steps[0]["map_steps"]
        assert [len(item["records"]) for item in rows] == [2] * size
        assert all(link["record_model"] and link["record_id"] for item in rows for link in item["records"])
    assert counts[0] == counts[1], counts


def test_graph_prefetches_real_decision_and_child_relations(schema, review, child_graph):
    """Prefetched relations keep the full card and child projections their resolvers serve."""
    actor, _, _, _ = review
    decision_run, decision_step = start_review(review)
    decision = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph_step_runs { decision {
        id permissions records { id record_model record_id }
        requester { display_name } assignees { display_name } answered_by { display_name }
      } } }
    }""", {"id": decision_run.sqid}, user=actor))["workflowrun_by_pk"]["graph_step_runs"][0]["decision"]
    assert decision["id"] and decision["records"] and decision["assignees"]
    assert decision["records"][0]["record_model"] and decision["records"][0]["record_id"]
    assert decision_step.decision_id

    child_actor, _, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    steps = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph_step_runs {
        id awaited_run { id display_name } child_runs { id display_name status }
      } }
    }""", {"id": parent.sqid}, user=child_actor))["workflowrun_by_pk"]["graph_step_runs"]
    awaited = [step["awaited_run"] for step in steps if step["awaited_run"]]
    children = [run for step in steps for run in step["child_runs"]]
    assert awaited and children and admitted
    assert all(run["display_name"] for run in (*awaited, *children))


def test_drill_in_decision_steps_read_their_decisions_once(schema, review):
    """Asked decisions join the step batch; their card details stay with the decisions owner."""
    actor, people, sent, question = review
    query = """query($id: String!) { workflowrun_by_pk(id: $id) {
      graph_step_runs { records { id record_model record_id } decision { id kind_label } }
    } }"""
    counts = []
    for size in (2, 6):
        document = chain(question.key, size, outcome="approved")
        workflow = load_workflow(document, key=f"drill-decision-{size}", actor=actor)
        run = start_run(workflow, actor=actor, subject=question.review_subject)
        for index in range(size):
            run_until(run)
            asked = StepRun.system_queryset().get(run=run, node_key=f"node_{index}").decision
            decide(asked, actor=people[0], chosen=["approve"])
            assert deliver_decision_wakes(sent, asked.pk) == [1]
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            steps = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))[
                "workflowrun_by_pk"
            ]["graph_step_runs"]
        counts.append(len(queries))
        assert len(steps) == size
        assert all(step["decision"] and step["records"] for step in steps)
    assert counts[0] == counts[1], counts


def test_drill_in_map_item_decisions_are_prefetched(schema, review, settings):
    actor, _, _, question = review
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 6
    counts = []
    for size in (2, 6):
        workflow = load_workflow({"nodes": {
            "items": {"step": "map", "body": {"step": question.key},
                      "input": {"items": {"from": "input", "path": ["items"]}}},
        }}, key=f"drill-map-decision-{size}", actor=actor)
        run = start_run(workflow, actor=actor, subject=question.review_subject, input={"items": [{}] * size})
        run_until(run)
        with CaptureQueriesContext(connection) as queries:
            items = result_data(execute_schema(schema, """query($id: String!) {
              workflowrun_by_pk(id: $id) { graph_step_runs { map_steps {
                id records { id record_model record_id } decision { id }
              } } }
            }""", {"id": run.sqid}, user=actor))["workflowrun_by_pk"]["graph_step_runs"][0]["map_steps"]
        counts.append(len(queries))
        assert len(items) == size and all(item["records"] and item["decision"] for item in items)
    assert counts[0] == counts[1], counts
    cards = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { graph_step_runs { map_steps {
        decision { id records { id record_model record_id } assignees { display_name } }
      } } }
    }""", {"id": run.sqid}, user=actor))["workflowrun_by_pk"]["graph_step_runs"][0]["map_steps"]
    assert all(item["decision"]["records"] and item["decision"]["assignees"] for item in cards)
