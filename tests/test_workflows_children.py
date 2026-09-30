"""Child admission and lifecycle through the public execution owners."""

from datetime import timedelta

import pytest
from django.db.models.functions import Now
from pydantic import BaseModel

from angee.base.scoping import system_queryset
from angee.workflows.awaits import AwaitRunInput
from angee.workflows.managers import WorkflowRunQuerySet
from angee.workflows.runner import runner
from angee.workflows.states import RunOrigin
from angee.workflows.steps import Step, StepMode
from angee.workflows.testing.drivers import capture_tasks, load_workflow, observe, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.conftest import create_user
from tests.workflow_steps import Value, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def child_graph(execution, register_step):
    """Build parents whose child admission is performed by a real claimed step."""
    actor, sent = execution
    admitted = []

    def build(*, relation="owned", await_child=True, repeat=False, next_page=False, request_key=None, subject=None,
              subject_model="", run_actor=None, grant_child=True, child_step="echo"):
        child_workflow = load_workflow(
            document("entry", step=child_step), key=f"child-{len(admitted)}", actor=actor,
            subject_model=subject_model,
        )

        class StartChild(Step[Value, AwaitRunInput, None]):
            """Start again after a time wait when exercising derived-key replay."""

            key = "start_child"

            def run(self, ctx):
                child = ctx.start_run(
                    child_workflow, input=ctx.input.model_dump(), relation=relation,
                    request_key=request_key, subject=subject,
                )
                admitted.append(child)
                if repeat and not ctx.state:
                    if next_page:
                        return ctx.next_page({"again": True})
                    return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"again": True})
                return ctx.done({"run_id": str(child.sqid)})

        class Finish(Step[None, None, None]):
            """Close every observed child outcome without requiring a value on cancellation."""

            key = "finish_child"

            def run(self, ctx):
                return ctx.done(ctx.input)

        register_step(StartChild)
        register_step(Finish)
        nodes = {"start": {"step": StartChild.key}}
        if await_child:
            nodes["start"]["next"] = {"done": "await"}
            nodes["await"] = {
                "step": "await_run", "config": {"expects": child_workflow.key},
                "next": {outcome: "finish" for outcome in ("done", "error", "canceled")},
            }
            nodes["finish"] = {"step": Finish.key}
        parent_workflow = load_workflow({"nodes": nodes}, key=f"parent-{len(admitted)}", actor=actor)
        if run_actor is not None:
            parent_workflow.with_actor(actor).grant_record_access("starter", run_actor)
            if grant_child:
                child_workflow.with_actor(actor).grant_record_access("starter", run_actor)
        parent = WorkflowRun.objects.start(parent_workflow, actor=run_actor or actor, input={"value": 7})
        return parent, child_workflow

    return actor, sent, admitted, build


def test_await_run_wakes_once_and_forwards_the_child_output(child_graph):
    actor, sent, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    child = admitted[0]
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert waiter.status == "waiting" and waiter.waiting_kind == "run" and waiter.awaited_run_id == child.pk
    assert child.origin == RunOrigin.WORKFLOW and child.parent_step.run_id == parent.pk
    sent.clear()
    run_until(child)
    assert [(name, payload["kwargs"]) for name, payload in sent if name == "workflows.wake_run"] == [
        ("workflows.wake_run", {"run_id": child.pk}),
    ]
    assert runner.wake_runs(child.pk) == 1
    assert runner.wake_runs(child.pk) == 0
    run_until(parent)
    waiter.refresh_from_db()
    assert parent.status == "succeeded" and waiter.output == {"value": 7} and waiter.outcome == "done"
    assert system_queryset(StepAttempt).filter(step_run=waiter).count() == 2
    assert not parent.can_cancel(actor)


@pytest.mark.parametrize("relation", ["owned", "continuation"])
def test_parent_cancel_follows_only_owned_children(child_graph, relation):
    actor, _, admitted, build = child_graph
    parent, _ = build(relation=relation)
    run_until(parent)
    child = admitted[0]
    result = WorkflowRun.objects.cancel(parent, actor=actor)
    child.refresh_from_db()
    assert result.canceled
    assert result.children == (1 if relation == "owned" else 0)
    assert ("1 child run canceled" in result.message) == (relation == "owned")
    assert child.status == ("canceled" if relation == "owned" else "running")
    if relation == "continuation":
        run_until(child)
        assert child.status == "succeeded"


@pytest.mark.parametrize("relation", ["owned", "continuation"])
def test_parent_finishing_without_await_cancels_only_owned_child(child_graph, relation):
    _, _, admitted, build = child_graph
    parent, _ = build(relation=relation, await_child=False)
    run_until(parent)
    child = system_queryset(WorkflowRun).get(pk=admitted[0].pk)
    assert parent.status == "succeeded"
    assert child.status == ("canceled" if relation == "owned" else "running")


@pytest.mark.parametrize("request_key", [None, "caller-child-key"])
def test_child_reexecution_replays_pinned_request_after_republish(child_graph, request_key, register_step):
    actor, _, admitted, build = child_graph
    parent, workflow = build(repeat=True, request_key=request_key)
    run_until(parent)
    first = admitted[0]
    class ChangedInput(BaseModel):
        label: str

    class ChangedContract(Step[ChangedInput, Value, None]):
        key = "changed_child_contract"

        def run(self, ctx):
            return ctx.done({"value": len(ctx.input.label)})

    register_step(ChangedContract)
    replacement = load_workflow(document("new_entry", step=ChangedContract.key), key=workflow.key, actor=actor)
    assert replacement.published_id != first.version_id
    assert runner.tick()["woken"] == 1
    run_until(parent)
    assert len(admitted) == 2 and admitted[1].pk == first.pk
    assert admitted[1].version_id == first.version_id
    assert first.request_key == (request_key or f"child:{first.parent_step.sqid}:{first.parent_step.page_index}")
    assert system_queryset(WorkflowRun).filter(parent_step__run=parent).count() == 1


def test_derived_child_key_changes_with_parent_page(child_graph):
    actor, _, admitted, build = child_graph
    parent, _ = build(repeat=True, next_page=True)
    run_until(parent)
    first = admitted[0]
    second = admitted[1]
    assert first.pk != second.pk
    assert first.request_key != second.request_key
    assert first.parent_step_id == second.parent_step_id


def test_child_cleanup_failure_cannot_roll_back_parent_terminal_state(child_graph, monkeypatch, caplog):
    _, _, admitted, build = child_graph
    parent, _ = build(await_child=False)
    original = WorkflowRunQuerySet.hold_owned

    def unavailable(self, run_id, **kwargs):
        if run_id == admitted[0].pk:
            raise RuntimeError("Child cancellation unavailable")
        return original(self, run_id, **kwargs)

    monkeypatch.setattr(WorkflowRunQuerySet, "hold_owned", unavailable)
    run_until(parent)
    assert system_queryset(WorkflowRun).get(pk=parent.pk).status == "succeeded"
    assert "Child cancellation unavailable" in caplog.text


@pytest.mark.parametrize("missing", ["start", "subject_read"])
def test_child_admission_enforces_start_and_subject_read(child_graph, missing):
    _, _, admitted, build = child_graph
    starter = create_user(f"child-{missing}")
    # A workflow identity is a neutral, access-controlled subject record.
    subject = load_workflow(document("subject"), key="private-subject", actor=child_graph[0])
    parent, _ = build(
        run_actor=starter, grant_child=missing != "start", subject=subject,
        subject_model="workflows.workflow",
    )
    run_until(parent)
    assert parent.status == "failed" and not admitted
    attempt = system_queryset(StepAttempt).get(step_run__run=parent)
    assert attempt.result == "failed"
    assert ("Read access" if missing == "subject_read" else "'start'") in attempt.error
    assert not system_queryset(WorkflowRun).filter(parent_step__run=parent).exists()


@pytest.mark.parametrize("child_step, outcome", [("reject", "error"), ("echo", "canceled")])
def test_await_forwards_empty_failure_and_cancellation_outcomes(child_graph, child_step, outcome):
    actor, _, admitted, build = child_graph
    parent, _ = build(child_step=child_step)
    run_until(parent)
    child = admitted[0]
    if outcome == "canceled":
        WorkflowRun.objects.cancel(child, actor=actor)
    else:
        run_until(child)
    assert runner.tick()["runs"] == 1
    run_until(parent)
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert parent.status == "succeeded" and waiter.outcome == outcome and waiter.output == {}


def age_runs(*runs):
    """Move finished rows beyond retention without manufacturing execution state."""
    system_queryset(WorkflowRun).filter(pk__in=[run.pk for run in runs]).update(
        finished_at=Now() - timedelta(days=91),
    )


def test_expired_io_attempt_cannot_admit_a_child_before_reaping(execution, register_step):
    """Child admission shares the deadline fence used by all live attempt writes."""
    actor, _ = execution
    child_workflow = load_workflow(document("entry"), key="late-child", actor=actor)

    class LateChild(Step[None, None, None]):
        key = "late_child"
        mode = StepMode.IO

        def run(self, ctx):
            system_queryset(StepRun).filter(pk=ctx.step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))
            ctx.start_run(child_workflow)
            return ctx.done({})

    register_step(LateChild)
    workflow = load_workflow({"nodes": {"entry": {"step": LateChild.key}}}, actor=actor)
    parent = WorkflowRun.objects.start(workflow, actor=actor)
    row = system_queryset(StepRun).get(run=parent)
    assert runner.execute(row.pk) is False
    assert not system_queryset(WorkflowRun).filter(parent_step__run=parent).exists()
    assert runner.tick()["reaped"] == 1


def test_observing_an_already_failed_child_keeps_its_open_siblings(child_graph):
    """Await evidence is retained even when no suspension is needed."""
    actor, _, admitted, build = child_graph
    parent, workflow = build()
    load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"done": ["reject", "z_pending"]}},
        "reject": {"step": "reject"}, "z_pending": {"step": "pause"},
    }}, key=workflow.key, actor=actor)
    run_until(parent, node="await")
    child = admitted[0]
    run_until(child)
    pending = system_queryset(StepRun).get(run=child, node_key="z_pending")
    assert child.status == "failed" and pending.status == "ready"
    run_until(parent)
    pending.refresh_from_db()
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    assert parent.status == "succeeded" and pending.status == "ready"
    assert waiter.awaited_run_id == child.pk and waiter.outcome == "error"
    assert parent.can_cancel(actor)
    result = WorkflowRun.objects.cancel(parent, actor=actor)
    pending.refresh_from_db()
    child.refresh_from_db()
    assert not result.canceled and result.steps == 1 and pending.status == "canceled"
    assert child.status == "failed" and result.children == 0


def test_child_delivery_failures_cannot_suppress_parent_publication(child_graph, caplog):
    """Nested dispatch and terminal wake failures leave all committed changes observable."""
    _, _, admitted, build = child_graph
    parent, _ = build()
    with observe(WorkflowRun) as published, capture_tasks(
        error=RuntimeError("Broker unavailable during child delivery."),
    ):
        run_until(parent)
        assert sum(payload.id == str(parent.sqid) for payload in published) == 2
        assert any(payload.id == str(admitted[0].sqid) for payload in published)
        published.clear()
        run_until(admitted[0])
        assert [payload.id for payload in published] == [str(admitted[0].sqid)]
        assert runner.wake_runs(admitted[0].pk) == 1
        run_until(parent)
        assert parent.status == "succeeded"
        assert sum(payload.id == str(parent.sqid) for payload in published) == 3
    assert "Broker unavailable during child delivery" in caplog.text
