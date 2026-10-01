"""Map progress and ordered item rows through the existing read resource."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from pydantic import BaseModel

from angee.base.scoping import system_queryset
from angee.workflows import schema as workflow_schema
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.workflow_steps import Value

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


class Items(BaseModel):
    """An ordinary step may legitimately own the same input field as a map."""

    items: list[Value]


class PrepareItems(Step[Items, Items, None]):
    """Retain typed items so publishing proves the body's default item binding."""

    key = "surface_prepare_items"

    def run(self, ctx):
        return ctx.done(ctx.input)


@pytest.fixture
def map_workflow(execution, register_step):
    """Publish a real map behind an ordinary list-producing step."""
    actor, _sent = execution
    register_step(PrepareItems)
    return load_workflow({
        "nodes": {
            "prepare": {"step": PrepareItems.key, "next": {"done": "items"}},
            "items": {
                "step": "map", "body": {"step": "echo"},
                "input": {"items": {"from": "prepare", "path": ["items"]}},
                "next": {"done": "finish", "failed": "finish"},
            },
            "finish": {"step": "echo", "input": {"value": {"value": 9}}},
        },
        "results": [{"from": "finish"}],
    }, actor=actor)


@pytest.fixture
def schema():
    return addon_schema(workflow_schema.schemas, "console")


def test_map_progress_and_item_identity_follow_retained_execution(schema, map_workflow, execution, settings):
    """Progress tracks only this run while item rows keep their containing rank."""
    admin, _sent = execution
    settings.ANGEE_WORKFLOW_MAP_CONCURRENCY = 1
    starter, outsider, operator = (create_user(name) for name in ("map-starter", "map-outsider", "map-operator"))
    for actor in (starter, outsider):
        map_workflow.with_actor(admin).grant_record_access("starter", actor)
    run = start_run(map_workflow, actor=starter, input={"items": [{"value": 2}, {"value": 3}]})
    run.with_actor(admin).grant_record_access("operator", operator)
    foreign = start_run(map_workflow, actor=outsider, input={"items": [{"value": 4}]})
    run_until(foreign)
    run_until(run, node="items")
    parent = system_queryset(StepRun).get(run=run, node_key="items")
    runner.execute(parent.pk)
    parent.refresh_from_db()
    assert (parent.map_total, parent.map_settled) == (2, 0)
    first = system_queryset(StepRun).get(run=run, node_key="items.body", map_index=0)
    assert (first.rank, first.is_mapped, first.map_total, first.map_settled) == (parent.rank, True, 0, 0)
    runner.execute(first.pk)
    parent.refresh_from_db()
    assert (parent.map_total, parent.map_settled) == (2, 1)
    query = """query($id: String!) {
      steprun(where: {run: {_eq: $id}}, order_by: [{rank: asc}, {map_index: asc}]) {
        node_key rank map_index is_mapped map_total map_settled
      }
      workflowrun_by_pk(id: $id) { version { id } step_runs { node_key map_total map_settled } }
    }"""
    visible = result_data(execute_schema(schema, query, {"id": run.sqid}, user=starter))
    assert visible["steprun"] == [
        {"node_key": "prepare", "rank": 0, "map_index": 0, "is_mapped": False, "map_total": 0, "map_settled": 0},
        {"node_key": "items", "rank": 1, "map_index": 0, "is_mapped": False, "map_total": 2, "map_settled": 1},
        {"node_key": "items.body", "rank": 1, "map_index": 0, "is_mapped": True, "map_total": 0, "map_settled": 0},
        {"node_key": "items.body", "rank": 1, "map_index": 1, "is_mapped": True, "map_total": 0, "map_settled": 0},
    ]
    assert visible["workflowrun_by_pk"]["step_runs"] == [
        {key: row[key] for key in ("node_key", "map_total", "map_settled")} for row in visible["steprun"]
    ]
    operator_view = result_data(execute_schema(schema, query, {"id": run.sqid}, user=operator))
    assert operator_view["steprun"] == visible["steprun"]
    assert operator_view["workflowrun_by_pk"] == {
        "version": None, "step_runs": visible["workflowrun_by_pk"]["step_runs"],
    }
    assert result_data(execute_schema(schema, query, {"id": run.sqid}, user=outsider)) == {
        "steprun": [], "workflowrun_by_pk": None,
    }
    run_until(run)
    parent.refresh_from_db()
    assert run.status == "succeeded"
    assert (parent.map_total, parent.map_settled) == (2, 2)


def test_map_progress_projection_query_cost_does_not_grow_with_item_count(schema, map_workflow, execution):
    """Native annotations keep the progress projection a fixed number of reads."""
    actor, _sent = execution
    runs = [start_run(map_workflow, actor=actor, input={"items": [{"value": i} for i in range(size)]})
            for size in (1, 12)]
    for run in runs:
        run_until(run)
    query = """query($id: String!) {
      steprun(where: {run: {_eq: $id}}) { node_key map_total map_settled }
    }"""
    counts = []
    for run in runs:
        with CaptureQueriesContext(connection) as queries:
            rows = result_data(execute_schema(schema, query, {"id": run.sqid}, user=actor))["steprun"]
        counts.append(len(queries))
        parent = next(row for row in rows if row["node_key"] == "items")
        assert parent["map_total"] == parent["map_settled"] == len(run.input["items"])
    assert counts[0] == counts[1]
    assert {"map_total", "map_settled"} <= schema._schema.get_type("StepRunType").fields.keys()
