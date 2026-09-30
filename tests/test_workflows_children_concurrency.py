"""Real PostgreSQL races between parent waits, child commits, and tree cancellation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection

from angee.base.scoping import system_queryset
from angee.workflows.awaits import AwaitRun
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.test_workflows_children import age_runs
from tests.test_workflows_children import child_graph as child_graph
from tests.test_workflows_review_concurrency import submit, wait_for_lock
from tests.workflow_steps import Echo

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_child_finishes_between_await_read_and_wait_commit(child_graph, monkeypatch):
    """A missed after-commit wake is recovered exactly once by the sweep."""
    _, sent, admitted, build = child_graph
    parent, _ = build()
    run_until(parent, node="await")
    child = admitted[0]
    waiter = system_queryset(StepRun).get(run=parent, node_key="await")
    observed, release = Event(), Event()
    body = AwaitRun.run

    def pause_after_read(self, ctx):
        settlement = body(self, ctx)
        observed.set()
        assert release.wait(10)
        return settlement

    monkeypatch.setattr(AwaitRun, "run", pause_after_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker, _ = submit(pool, lambda: runner.execute(waiter.pk))
        try:
            assert observed.wait(10)
            run_until(child)
            assert child.status == "succeeded"
            # The terminal task cannot yet see a committed waiter.
            assert runner.wake_runs(child.pk) == 0
        finally:
            release.set()
        assert worker.result(timeout=10)
    sent.clear()
    assert runner.tick()["runs"] == 1
    assert runner.tick()["runs"] == 0
    assert [payload["kwargs"]["step_run_id"] for name, payload in sent if name == "workflows.execute"] == [waiter.pk]
    run_until(parent)
    assert parent.status == "succeeded"
    assert system_queryset(StepAttempt).filter(step_run=waiter).count() == 2


def test_parent_cancel_waits_for_child_final_settlement_without_overwriting_it(child_graph, monkeypatch):
    """Ancestor-first cancel keeps a child's terminal success when its body wins."""
    actor, _, admitted, build = child_graph
    parent, _ = build()
    run_until(parent)
    child = admitted[0]
    child_step = system_queryset(StepRun).get(run=child)
    entered, release = Event(), Event()
    body = Echo.run

    def pause_before_return(self, ctx):
        entered.set()
        assert release.wait(10)
        return body(self, ctx)

    monkeypatch.setattr(Echo, "run", pause_before_return)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker, _ = submit(pool, lambda: runner.execute(child_step.pk))
        try:
            assert entered.wait(10)
            cancel, pid = submit(pool, lambda: WorkflowRun.objects.cancel(parent, actor=actor))
            wait_for_lock(pid, cancel)
        finally:
            release.set()
        assert worker.result(timeout=10)
        result = cancel.result(timeout=10)
    parent.refresh_from_db()
    child.refresh_from_db()
    assert parent.status == "canceled" and child.status == "succeeded"
    assert child.output == {"value": 7} and result.canceled and result.children == 0
    assert system_queryset(StepRun).get(run=parent, node_key="await").status == "canceled"
    assert system_queryset(StepAttempt).get(step_run=child_step).result == "succeeded"
    assert runner.wake_runs(child.pk) == 0


def test_prune_skips_locked_terminal_continuation_then_retries_without_deleting_it(child_graph):
    """A busy surviving child marks its parent instead of blocking the retention tick."""
    _, _, admitted, build = child_graph
    parent, _ = build(relation="continuation", await_child=False)
    run_until(parent)
    child = admitted[0]
    run_until(child)
    assert parent.status == "succeeded" and child.status == "succeeded"
    age_runs(parent)
    entered, release = Event(), Event()

    def hold_continuation():
        with WorkflowRun.objects.hold(child.pk):
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        holder, _ = submit(pool, hold_continuation)
        try:
            assert entered.wait(10)
            pruning, _ = submit(pool, WorkflowRun.objects.prune)
            assert pruning.result(timeout=5) == 0
            parent.refresh_from_db()
            child.refresh_from_db()
            assert parent.prune_after is not None and parent.prune_reason
            assert child.parent_step_id is not None and child.status == "succeeded"
        finally:
            release.set()
        holder.result(timeout=10)

    system_queryset(WorkflowRun).filter(pk=parent.pk).update(prune_after=None, prune_reason="")
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(WorkflowRun).filter(pk=parent.pk).exists()
    child.refresh_from_db()
    assert child.parent_step_id is None and child.status == "succeeded"
    assert child.output == {"value": 7}
