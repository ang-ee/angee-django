"""Real PostgreSQL cross-run cancellation after the caller releases its locks."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection

from angee.base.scoping import system_queryset
from angee.workflows import tasks
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from tests.test_workflows_cancel import cancellations
from tests.test_workflows_review_concurrency import submit, wait_for_lock
from tests.workflow_steps import Echo, document

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.mark.parametrize("case", ["cancel", "target_finished", "caller_rollback"])
def test_cross_run_cancellation_releases_the_caller_before_locking_the_target(execution, register_step, case):
    """Commit delivers once, final target settlement wins, and rollback delivers nothing."""
    actor, sent = execution
    target_entered, release_target = Event(), Event()
    requested, release_caller = Event(), Event()

    class BlockingTarget(Echo):
        key = "blocking_cancel_target"

        def run(self, ctx):
            target_entered.set()
            assert release_target.wait(10)
            return ctx.done(ctx.input)

    class CancelOther(Echo):
        key = "cancel_other"

        def run(self, ctx):
            ctx.cancel_run(ctx.load(WorkflowRun, target.sqid))
            requested.set()
            assert release_caller.wait(10)
            return ctx.fail("Discard this body.") if case == "caller_rollback" else ctx.done(ctx.input)

    register_step(BlockingTarget)
    register_step(CancelOther)
    target_nodes = ("entry",) if case == "target_finished" else ("entry", "finish")
    target_workflow = load_workflow(document(*target_nodes, step=BlockingTarget.key), key="target", actor=actor)
    target = WorkflowRun.objects.start(target_workflow, actor=actor, input={"value": 12})
    caller_workflow = load_workflow(document("entry", step=CancelOther.key), key="caller", actor=actor)
    caller = WorkflowRun.objects.start(caller_workflow, actor=actor)
    target_step = system_queryset(StepRun).get(run=target)
    caller_step = system_queryset(StepRun).get(run=caller)
    sent.clear()

    with ThreadPoolExecutor(max_workers=3) as pool:
        target_worker, _ = submit(pool, lambda: StepRun.objects.execute(target_step.pk))
        try:
            assert target_entered.wait(10)
            caller_worker, _ = submit(pool, lambda: StepRun.objects.execute(caller_step.pk))
            assert requested.wait(10), "Requesting cancellation must not lock the unrelated target."
            assert cancellations(sent) == []
            release_caller.set()
            assert caller_worker.result(timeout=10)
            caller.refresh_from_db()
            assert caller.status == ("failed" if case == "caller_rollback" else "succeeded")
            delivered = cancellations(sent)
            assert len(delivered) == (0 if case == "caller_rollback" else 1)
            if delivered:
                cancel_worker, pid = submit(pool, lambda: tasks.cancel(**delivered[0]))
                wait_for_lock(pid, cancel_worker)
        finally:
            release_caller.set()
            release_target.set()
        assert target_worker.result(timeout=10)
        if delivered:
            cancel_worker.result(timeout=10)

    target.refresh_from_db()
    expected = {"cancel": "canceled", "target_finished": "succeeded", "caller_rollback": "running"}
    assert target.status == expected[case]
    assert system_queryset(StepAttempt).get(step_run=target_step).result == "succeeded"
    if case == "target_finished":
        assert target.outcome == "done" and target.output == {"value": 12}
    else:
        successor = system_queryset(StepRun).get(run=target, node_key="finish")
        assert successor.status == ("ready" if case == "caller_rollback" else "canceled")
        assert not system_queryset(StepAttempt).filter(step_run=successor).exists()
    if delivered:
        terminal = (target.status, target.outcome, target.output, target.finished_at)
        tasks.cancel(**delivered[0])
        target.refresh_from_db()
        assert (target.status, target.outcome, target.output, target.finished_at) == terminal
