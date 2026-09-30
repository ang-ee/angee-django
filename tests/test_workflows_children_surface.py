"""Child execution links use the normal run policy and resource relations."""

import pytest

from angee.base.scoping import system_queryset
from angee.workflows import schema as workflow_schema
from angee.workflows.awaits import AwaitRunInput
from angee.workflows.runner import runner
from angee.workflows.steps import Step
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepRun, WorkflowRun
from tests.conftest import addon_schema, create_user, execute_schema, result_data
from tests.workflow_steps import document

pytestmark = pytest.mark.usefixtures("workflow_step_classes")


@pytest.fixture
def schema():
    """Build the addon's read surface through its resource owner."""
    return addon_schema(workflow_schema.schemas, "console")


@pytest.fixture
def child_workflows(execution, register_step):
    """A real parent starts and awaits the existing time-wait fixture."""
    actor, _sent = execution
    child = load_workflow(document("pause", step="pause"), key="surface-child", actor=actor)

    class StartChild(Step[None, AwaitRunInput, None]):
        key = "surface_start_child"

        def run(self, ctx):
            run = ctx.start_run(child, relation="owned")
            return ctx.done(AwaitRunInput(run_id=run.sqid))

    class Finish(Step[None, None, None]):
        key = "surface_finish_child"

        def run(self, ctx):
            return ctx.done()

    register_step(StartChild)
    register_step(Finish)
    parent = load_workflow({
        "nodes": {
            "start": {"step": StartChild.key, "next": {"done": "await"}},
            "await": {"step": "await_run", "config": {"expects": child.key},
                      "input": {"from": "start"},
                      "next": {"done": "finish", "error": "finish", "canceled": "finish"}},
            "finish": {"step": Finish.key},
        },
        "results": [{"from": "finish"}],
    }, key="surface-parent", actor=actor)
    return parent, child


def test_child_reads_inherit_the_parent_rule_without_write_or_workflow_access(schema, child_workflows, execution):
    """A parent reader sees descendants; another starter cannot see either run."""
    admin, _sent = execution
    parent_workflow, child_workflow = child_workflows
    starter, viewer, outsider = (create_user(name) for name in (
        "child-surface-starter", "child-surface-viewer", "child-surface-outsider",
    ))
    for workflow in (parent_workflow, child_workflow):
        for user in (starter, outsider):
            workflow.with_actor(admin).grant_record_access("starter", user)
    parent = start_run(parent_workflow, actor=starter)
    run_until(parent)
    parent.with_actor(admin).grant_record_access("reader", viewer)
    start = system_queryset(StepRun).get(run=parent, node_key="start")
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    child = system_queryset(WorkflowRun).get(parent_step=start)
    query = """query($parent: String!, $child: String!, $step: String!) {
      workflowrun(where: {parent_step__run: {_eq: $parent}}) {
        id origin can_cancel can_reprocess version { id }
        parent_step { id run { id } }
      }
      workflowrun_aggregate(where: {parent_step__run: {_eq: $parent}}) { aggregate { count } }
      workflowrun_by_pk(id: $parent) { step_runs { node_key child_runs { id } } }
      steprun(where: {awaited_run: {_eq: $child}, waiting_kind: {_eq: "run"}}) {
        id waiting_kind awaited_run { id }
      }
      from_step: workflowrun(where: {parent_step: {_eq: $step}}) { id }
    }"""
    variables = {"parent": parent.sqid, "child": child.sqid, "step": start.sqid}
    visible = result_data(execute_schema(schema, query, variables, user=viewer))
    assert visible == {
        "workflowrun": [{
            "id": child.sqid, "origin": "WORKFLOW", "can_cancel": False, "can_reprocess": False,
            "version": None, "parent_step": {"id": start.sqid, "run": {"id": parent.sqid}},
        }],
        "workflowrun_aggregate": {"aggregate": {"count": 1}},
        "workflowrun_by_pk": {"step_runs": [
            {"node_key": "start", "child_runs": [{"id": child.sqid}]},
            {"node_key": "await", "child_runs": []},
        ]},
        "steprun": [{"id": waiter.sqid, "waiting_kind": "RUN", "awaited_run": {"id": child.sqid}}],
        "from_step": [{"id": child.sqid}],
    }
    assert result_data(execute_schema(schema, query, variables, user=outsider)) == {
        "workflowrun": [], "workflowrun_aggregate": {"aggregate": {"count": 0}},
        "workflowrun_by_pk": None, "steprun": [], "from_step": [],
    }
    resources = {resource.model_label: resource for resource in schema.angee_resources}
    assert resources["workflows.WorkflowRun"].query.fields["parent_step.run"].filter is not None
    assert resources["workflows.WorkflowRun"].query.fields["parent_step"].filter is not None
    assert resources["workflows.StepRun"].query.fields["awaited_run"].filter is not None


def test_a_child_reader_does_not_gain_its_parent_or_starting_step(schema, child_workflows, execution):
    """Inheritance flows to descendants, while relation projection gates each target."""
    admin, _sent = execution
    parent_workflow, _child_workflow = child_workflows
    parent = start_run(parent_workflow, actor=admin)
    run_until(parent)
    child = system_queryset(WorkflowRun).get(parent_step__run=parent)
    reader = create_user("child-only-reader")
    child.with_actor(admin).grant_record_access("reader", reader)
    query = """query($parent: String!, $child: String!) {
      parent: workflowrun_by_pk(id: $parent) { id }
      child: workflowrun_by_pk(id: $child) { id parent_step { id run { id } } }
    }"""
    assert result_data(execute_schema(schema, query, {"parent": parent.sqid, "child": child.sqid}, user=reader)) == {
        "parent": None, "child": {"id": child.sqid, "parent_step": None},
    }


def test_await_executes_with_run_read_without_access_to_its_workflow(schema, execution):
    """The public read grant is sufficient; pinned policy reads stay with the model."""
    admin, _sent = execution
    reader = create_user("await-run-reader")
    child_workflow = load_workflow(document("entry"), key="private-await-definition", actor=admin)
    child = start_run(child_workflow, actor=admin, input={"value": 7})
    child.with_actor(admin).grant_record_access("reader", reader)
    parent_workflow = load_workflow({
        "nodes": {
            "await": {"step": "await_run", "config": {"expects": child_workflow.key},
                      "input": {"run_id": {"value": child.sqid}},
                      "next": {"done": "finish", "error": "finish", "canceled": "finish"}},
            "finish": {"step": "echo", "input": {"value": {"value": 1}}},
        },
        "results": [{"from": "finish"}],
    }, key="read-granted-await", actor=admin)
    parent_workflow.with_actor(admin).grant_record_access("starter", reader)
    projection = result_data(execute_schema(schema, """query($id: String!) {
      workflowrun_by_pk(id: $id) { id version { id } }
    }""", {"id": child.sqid}, user=reader))
    assert projection["workflowrun_by_pk"] == {"id": child.sqid, "version": None}
    parent = start_run(parent_workflow, actor=reader)
    run_until(parent)
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert (waiter.status, waiter.awaited_run_id) == ("waiting", child.pk)
    run_until(child)
    assert runner.wake_runs(child.pk) == 1
    run_until(parent)
    waiter.refresh_from_db()
    assert parent.status == "succeeded"
    assert (waiter.outcome, waiter.output) == ("done", {"value": 7})
