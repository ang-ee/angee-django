"""Reprocessing from a step: a new run on the pinned version reuses what the step cannot reach."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import actor_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows import schema as workflow_schema
from angee.workflows.definition import Definition
from angee.workflows.states import RunOrigin, RunStatus, StepRunStatus
from angee.workflows.testing.drivers import load_workflow, publish_draft, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.conftest import addon_schema, create_user
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("workflow_step_classes")]

FORK = {
    "nodes": {
        "entry": {"step": "echo", "next": {"done": ["left", "right"]}},
        "left": {"step": "echo", "next": {"done": "join"}},
        "right": {"step": "echo", "next": {"done": "join"}},
        "join": {"step": "echo", "join": "all", "input": {"from": "left"}},
    },
    "results": [{"from": "join"}],
}


def rows(run):
    """The run's step rows by node key, read elevated."""
    return {row.node_key: row for row in StepRun.system_queryset().filter(run=run)}


def retained(run):
    """Everything a reprocess must leave unchanged on the retained run."""
    current = WorkflowRun.system_queryset().get(pk=run.pk)
    return (current.status, current.output, sorted(
        (row.node_key, row.status, row.attempt, row.output) for row in rows(run).values()
    ), StepAttempt.system_queryset().filter(step_run__run=run).count())


def test_reprocess_from_a_step_reuses_what_it_cannot_reach_and_reruns_the_rest(execution):
    actor, _sent = execution
    workflow = load_workflow(FORK, key="fork-reprocess", actor=actor)
    run = run_until(start_run(workflow, actor=actor, input={"value": 4}))
    assert run.status == RunStatus.SUCCEEDED
    before = retained(run)
    publish_draft(workflow, draft=document("entry"), actor=actor)
    replacement = WorkflowRun.objects.reprocess(run, actor=actor, from_node="left")
    assert replacement.reprocess_of_id == run.pk and replacement.origin == RunOrigin.REPROCESS
    assert replacement.version_id == run.version_id and replacement.input == run.input
    copied = rows(replacement)
    assert set(copied) == {"entry", "right", "left"}
    for key in ("entry", "right"):
        assert copied[key].status == StepRunStatus.SUCCEEDED and copied[key].attempt == 0
        assert copied[key].output == rows(run)[key].output
        assert copied[key].notes[-1] == {"tone": "info", "message": f"Reused from {run}."}
        assert copied[key].decision_id is None and copied[key].awaited_run_id is None
    assert copied["left"].status == StepRunStatus.READY
    finished = run_until(replacement)
    assert finished.status == RunStatus.SUCCEEDED and finished.output == run.output
    executed = {row.node_key: row.attempt for row in rows(replacement).values()}
    assert executed == {"entry": 0, "right": 0, "left": 1, "join": 1}
    assert StepAttempt.system_queryset().filter(step_run__run=replacement).count() == 2
    assert retained(run) == before


def test_reprocess_from_a_step_reruns_an_unrouted_failure_it_cannot_reach(execution):
    actor, _sent = execution
    failing = {**FORK, "nodes": {**FORK["nodes"], "right": {"step": "reject", "next": {"done": "join"}}}}
    workflow = load_workflow(failing, key="fork-failure", actor=actor)
    run = run_until(start_run(workflow, actor=actor))
    assert run.status == RunStatus.FAILED and rows(run)["left"].status == StepRunStatus.SUCCEEDED
    before = retained(run)
    from_left = WorkflowRun.objects.reprocess(run, actor=actor, from_node="left")
    assert {key: row.status for key, row in rows(from_left).items()} == {
        "entry": StepRunStatus.SUCCEEDED, "left": StepRunStatus.READY, "right": StepRunStatus.READY,
    }
    from_right = WorkflowRun.objects.reprocess(run, actor=actor, from_node="right")
    reused = rows(from_right)
    assert reused["left"].status == StepRunStatus.SUCCEEDED and reused["left"].attempt == 0
    assert reused["right"].status == StepRunStatus.READY and "join" not in reused
    assert retained(run) == before


def test_reprocess_from_a_step_needs_a_terminal_run_and_a_step_that_ran(execution):
    actor, _sent = execution
    routed = load_workflow({
        "nodes": {
            "entry": {"step": "route", "config": {"outcome": "left"}, "next": {"left": "kept", "right": "unused"}},
            "kept": {"step": "echo"},
            "unused": {"step": "echo"},
        },
        "results": [{"from": "kept"}, {"from": "unused"}],
    }, key="routed-reprocess", actor=actor)
    run = run_until(start_run(routed, actor=actor))
    steps = rows(run)
    assert steps["unused"].status == StepRunStatus.SKIPPED
    assert steps["kept"].can_reprocess_from(actor) and not steps["unused"].can_reprocess_from(actor)
    for node in ("unused", "missing", "kept.body"):
        with pytest.raises(ValidationError, match="that ran"):
            WorkflowRun.objects.reprocess(run, actor=actor, from_node=node)
    with pytest.raises(ValidationError, match="Only a reprocess"):
        start_run(routed, actor=actor, from_node="kept")
    current = publish_draft(routed, draft=document("entry"), actor=actor).version
    with pytest.raises(ValidationError, match="reprocessed run's version"):
        start_run(routed, actor=actor, reprocess_of=run, version=current, from_node="kept")
    waiting = start_run(load_workflow(document("entry", step="pause"), key="paused-reprocess", actor=actor),
                        actor=actor)
    run_until(waiting)
    with pytest.raises(ValidationError, match="terminal"):
        WorkflowRun.objects.reprocess(waiting, actor=actor, from_node="entry")
    assert WorkflowRun.system_queryset().filter(reprocess_of__isnull=False).count() == 0


def test_reprocess_from_a_step_requires_run_write_and_workflow_start(execution):
    admin, _sent = execution
    operator = create_user("reprocess-step-operator")
    workflow = load_workflow(FORK, key="fork-operator", actor=admin)
    run = run_until(start_run(workflow, actor=admin))
    left = rows(run)["left"]
    assert not left.can_reprocess_from(operator)
    with pytest.raises(PermissionDenied, match="write"):
        WorkflowRun.objects.reprocess(run, actor=operator, from_node="left")
    run.with_actor(admin).grant_record_access("operator", operator)
    workflow.with_actor(admin).grant_record_access("starter", operator)
    assert left.can_reprocess_from(operator)
    with actor_context(operator):
        replacement = WorkflowRun.objects.reprocess(run.with_actor(operator), from_node="left")
    assert replacement.run_as_id == operator.pk != run.run_as_id


def test_reprocess_from_step_action_returns_the_replacement(execution):
    admin, _sent = execution
    operator, reader = create_user("step-action-operator"), create_user("step-action-reader")
    workflow = load_workflow(FORK, key="fork-action", actor=admin)
    workflow.with_actor(admin).grant_record_access("starter", operator)
    run = run_until(start_run(workflow, actor=admin))
    run.with_actor(admin).grant_record_access("operator", operator)
    run.with_actor(admin).grant_record_access("reader", reader)
    schema = addon_schema(workflow_schema.schemas, "console")
    left = rows(run)["left"]
    mutation = "mutation($id: ID!) { reprocess_workflow_run_from_step(id: $id) { ok id } }"
    projection = "query($id: String!) { steprun_by_pk(id: $id) { can_reprocess_from } }"
    assert result_data(execute_schema(schema, projection, {"id": left.sqid}, user=reader)) == {
        "steprun_by_pk": {"can_reprocess_from": False},
    }
    assert not result_data(execute_schema(schema, mutation, {"id": left.sqid}, user=reader))[
        "reprocess_workflow_run_from_step"
    ]["ok"]
    assert result_data(execute_schema(schema, projection, {"id": left.sqid}, user=operator)) == {
        "steprun_by_pk": {"can_reprocess_from": True},
    }
    result = result_data(execute_schema(schema, mutation, {"id": left.sqid}, user=operator))[
        "reprocess_workflow_run_from_step"
    ]
    replacement = WorkflowRun.system_queryset().get(reprocess_of=run)
    assert result == {"ok": True, "id": replacement.sqid} and replacement.run_as_id == operator.pk
    assert set(rows(replacement)) == {"entry", "right", "left"}


def test_definition_reuses_routed_settlements_outside_the_rerun_branch():
    definition = Definition.model_validate({
        "nodes": {
            "entry": {"step": "echo", "next": {"done": ["guarded", "open"]}},
            "guarded": {"step": "reject", "next": {"error": "fallback"}},
            "fallback": {"step": "echo"},
            "open": {"step": "echo"},
        },
        "results": [{"from": "fallback"}, {"from": "open"}],
    })

    def row(key, status, outcome=""):
        return SimpleNamespace(node_key=key, status=status, outcome=outcome)

    retained_rows = [
        row("entry", StepRunStatus.SUCCEEDED, "done"), row("guarded", StepRunStatus.FAILED, "error"),
        row("fallback", StepRunStatus.SUCCEEDED, "done"), row("open", StepRunStatus.WAITING),
    ]
    assert definition.descendants("guarded") == {"guarded", "fallback"}
    assert [item.node_key for item in definition.reusable_rows(retained_rows, "open")] == [
        "entry", "guarded", "fallback",
    ]
    assert [item.node_key for item in definition.reusable_rows(retained_rows, "fallback")] == ["entry", "guarded"]
    assert definition.reusable_rows(retained_rows, "entry") == []
    with pytest.raises(ValidationError, match="declared node"):
        definition.reusable_rows(retained_rows, "missing")
