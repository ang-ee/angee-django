"""PostgreSQL execution races using separate thread connections.

The run lock serializes body and settlement, so T2 cannot contain simultaneous
DATABASE settlements and T7 cannot contain a compliant concurrent superseder.
The tests name those boundaries explicitly instead of removing the run lock.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Barrier, Event, Lock, local
from time import monotonic, sleep

import psycopg
import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError, close_old_connections, connection, connections, transaction
from django.db.models import F
from django.db.models.functions import Now
from rebac import actor_context, system_context

from angee.base.scoping import system_queryset
from angee.jobs.enqueue import celery_app
from angee.workflows import tasks as workflow_tasks
from angee.workflows.definition import Definition
from angee.workflows.managers import StepAttemptQuerySet, StepRunQuerySet, WorkflowRunQuerySet
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import Retryable, RetryPolicy, StepMode
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import create_user, vault_for
from tests.workflow_steps import Echo, document

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(
        connection.vendor != "postgresql",
        reason="Real PostgreSQL row locks are required.",
    ),
]


def in_connection(fn, *args):
    """Run a worker on its own connection and close it even after a failed assertion."""
    close_old_connections()
    try:
        return fn(*args)
    finally:
        connections.close_all()


def row(run, key=None):
    """Read one durable step without changing the ambient worker principal."""
    query = system_queryset(StepRun).filter(run=run)
    return query.get(node_key=key) if key else query.get()


def test_same_request_key_concurrent_starts_return_one_run(execution):
    """Separate PostgreSQL connections converge on the committed unique request."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="same_key_start", actor=actor)
    ready = Barrier(3)

    def start():
        ready.wait(timeout=10)
        return WorkflowRun.objects.start(
            workflow, actor=actor, input={"value": 4}, request_key="test:concurrent_same_key",
        ).pk

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, start)
        second = pool.submit(in_connection, start)
        ready.wait(timeout=10)
        assert first.result(timeout=15) == second.result(timeout=15)
    assert system_queryset(WorkflowRun).filter(request_key="test:concurrent_same_key").count() == 1


def test_t1_duplicate_database_delivery_executes_one_body(execution, register_step):
    """Duplicate DATABASE messages race the run lock and only one reaches the body."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="claim", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    entered, release = Event(), Event()
    invocations = []

    class BlockingBody(Echo):
        def run(self, ctx):
            invocations.append(ctx.attempt.number)
            entered.set()
            assert release.wait(10), "The competing worker never released the first."
            return ctx.done(ctx.input)

    register_step(BlockingBody)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, runner.execute, step_run.pk)
        assert entered.wait(10)
        try:
            second = pool.submit(in_connection, runner.execute, step_run.pk)
            assert second.result(timeout=5) is False
            assert invocations == [1]
            assert row(run).status == StepRunStatus.READY  # Uncommitted claim is invisible.
        finally:
            release.set()
        assert first.result(timeout=10) is True
    assert not runner.execute(step_run.pk)
    assert system_queryset(StepAttempt).filter(step_run=step_run).count() == 1
    assert row(run).status == StepRunStatus.SUCCEEDED


def fanout(actor, key):
    """Admit two ready branches converging on an explicitly bound join."""
    workflow = load_workflow(
        {
            "nodes": {
                "entry": {"step": "echo", "next": {"done": ["left", "right"]}},
                "left": {"step": "echo", "next": {"done": "join"}},
                "right": {"step": "echo", "next": {"done": "join"}},
                "join": {"step": "echo", "join": "all", "input": {"from": "left"}},
            },
            "results": [{"from": "join"}],
        },
        key=key,
        actor=actor,
    )
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 12})
    assert runner.execute(row(run).pk)
    return run


def test_t2_serialized_branch_settlements_plan_one_join(execution, register_step):
    """Deliver overlapping branches and consume only their actual commit messages."""
    actor, sent = execution
    run = fanout(actor, "join")
    sent.clear()
    entered, release = Event(), Event()

    class BlockingBranch(Echo):
        def run(self, ctx):
            if ctx.step_run.node_key == "left":
                entered.set()
                assert release.wait(10)
            return ctx.done(ctx.input)

    register_step(BlockingBranch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, runner.execute, row(run, "left").pk)
        assert entered.wait(10)
        try:
            second = pool.submit(in_connection, runner.execute, row(run, "right").pk)
            assert second.result(timeout=5) is False
            assert sent == []
        finally:
            release.set()
        assert first.result(timeout=10)
    # No table scan drives recovery: only messages sent by committed advances.
    delivered = 0
    handlers = {task.name: task for task in (workflow_tasks.execute, workflow_tasks.wake_run)}
    while delivered < len(sent):
        assert delivered < 20, "Unexpected redispatch loop."
        name, envelope = sent[delivered]
        handlers[name](**envelope["kwargs"])
        delivered += 1
    assert row(run, "join").pk in [envelope["kwargs"]["step_run_id"] for name, envelope in sent
                                   if name == "workflows.execute"]
    assert row(run, "join").status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepRun).filter(run=run, node_key="join").count() == 1
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="join").count() == 1
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_t15_busy_branch_is_dispatched_on_holder_commit_without_tick(execution, register_step, monkeypatch):
    """A busy sibling gets a fresh commit-time send from its successful lock holder."""
    actor, _ = execution
    run = fanout(actor, "prompt_branch")
    left, right = row(run, "left"), row(run, "right")
    entered, release = Event(), Event()
    sends, mutex = [], Lock()

    def send(name, **kwargs):
        with mutex:
            sends.append(kwargs["kwargs"]["step_run_id"])

    class BlockingBranch(Echo):
        def run(self, ctx):
            if ctx.step_run.node_key == "left":
                entered.set()
                assert release.wait(10)
            return ctx.done(ctx.input)

    monkeypatch.setattr(celery_app, "send_task", send)
    register_step(BlockingBranch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, runner.execute, left.pk)
        assert entered.wait(10)
        try:
            dropped = pool.submit(in_connection, runner.execute, right.pk)
            assert dropped.result(timeout=5) is False
            assert sends == []
        finally:
            release.set()
        assert first.result(timeout=10)
        assert right.pk in sends, "Holder commit must restore the busy branch's delivery."
        second = pool.submit(in_connection, runner.execute, right.pk)
        assert second.result(timeout=5)
    assert row(run, "right").status == StepRunStatus.SUCCEEDED
    assert row(run, "right").dispatches == 0
    assert row(run, "join").status == StepRunStatus.READY


def test_t7_reentrant_fence_fault_rolls_back_database_body(execution, register_step):
    """Inject reentrant invalidation; compliant concurrent invalidation cannot occur."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="fence", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    original = workflow.name
    entered, release = Event(), Event()

    class InvalidatedBody(Echo):
        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Uncommitted domain write")
            with system_context(reason="test.inject_stale_fence"):
                StepRun.objects.filter(pk=ctx.step_run.pk).update(attempt=F("attempt") + 1)
            entered.set()
            assert release.wait(10)
            return ctx.done(ctx.input)

    register_step(InvalidatedBody)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(in_connection, runner.execute, row(run).pk)
        assert entered.wait(10)
        try:
            # Another connection sees neither the domain write nor a usable run lock.
            assert system_queryset(Workflow).get(pk=workflow.pk).name == original
            with transaction.atomic():
                locked = system_queryset(WorkflowRun).filter(pk=run.pk).lock_if_supported(no_key=True, skip_locked=True)
                assert not locked.exists()
        finally:
            release.set()
        assert future.result(timeout=10) is False
    assert system_queryset(Workflow).get(pk=workflow.pk).name == original
    assert row(run).status == StepRunStatus.READY and row(run).attempt == 0
    assert not system_queryset(StepAttempt).filter(step_run__run=run).exists()


@pytest.mark.parametrize("first_holder", ["worker", "tick"])
def test_t16_redispatch_and_settlement_follow_both_lock_orders(execution, register_step, monkeypatch, first_holder):
    """Both candidate orders skip a busy run and commit without a lock inversion."""
    actor, sent = execution
    workflow = load_workflow(document("entry"), key="tick_race", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with system_context(reason="test.old_delivery"):
        StepRun.objects.filter(pk=step_run.pk).update(dispatched_at=Now() - timedelta(seconds=61))
    entered, release = Event(), Event()
    role = local()
    original_hold = WorkflowRunQuerySet.hold

    @contextmanager
    def hold(self, run_id, *, skip_locked=False, **kwargs):
        with original_hold(self, run_id, skip_locked=skip_locked, **kwargs) as held:
            if getattr(role, "tick", False) and held is not None:
                entered.set()
                assert release.wait(10)
            yield held

    def tick():
        role.tick = True
        return runner.redispatch()

    class BlockingWorker(Echo):
        def run(self, ctx):
            if first_holder == "worker":
                entered.set()
                assert release.wait(10)
            return ctx.done(ctx.input)

    register_step(BlockingWorker)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        action = tick if first_holder == "tick" else lambda: runner.execute(step_run.pk)
        first = pool.submit(in_connection, action)
        assert entered.wait(10)
        try:
            if first_holder == "worker":
                assert system_queryset(StepRun).filter(pk=step_run.pk, status="ready").exists()
                assert pool.submit(in_connection, runner.redispatch).result(timeout=5) == 0
            else:
                assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=5) is False
        finally:
            release.set()
        assert first.result(timeout=10)
    if first_holder == "tick":
        assert sent[-1][1]["kwargs"]["step_run_id"] == step_run.pk
        assert runner.execute(step_run.pk)
    assert runner.redispatch() == 0
    assert row(run).status == StepRunStatus.SUCCEEDED and row(run).dispatches == 0
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_t13_plain_orm_query_is_scoped_to_actor(execution):
    """Plain ORM reads inside the body inherit the run principal and hide foreign rows."""
    admin, _ = execution
    actor, outsider = create_user("reader"), create_user("other")
    own = vault_for(actor, name="Visible")
    hidden = vault_for(outsider, name="Hidden")
    workflow = load_workflow(document("entry", step="visible"), key="scope", actor=admin)
    with system_context(reason="test.workflow_owner"):
        Workflow.objects.filter(pk=workflow.pk).update(created_by=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(in_connection, runner.execute, row(run).pk).result(timeout=10)
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 1}
    with actor_context(actor):
        assert type(own).objects.filter(pk=own.pk).exists()
        assert not type(hidden).objects.filter(pk=hidden.pk).exists()


def test_statement_timeout_rolls_back_body_and_restores_connection(execution, register_step):
    """A blocked domain write times out inside its savepoint and closes the attempt."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="timeout", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    class TimedOutWrite(Echo):
        timeout = timedelta(milliseconds=100)

        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Must roll back")
            return ctx.done(ctx.input)

    def execute_and_read_timeout(pk):
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            before = cursor.fetchone()[0]
        executed = runner.execute(pk)
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == before
        return executed

    register_step(TimedOutWrite)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            system_queryset(Workflow).filter(pk=workflow.pk).lock_if_supported(no_key=True).get()
            future = pool.submit(in_connection, execute_and_read_timeout, row(run).pk)
            assert future.result(timeout=10)
    assert row(run).status == StepRunStatus.FAILED
    assert "statement timeout" in system_queryset(StepAttempt).get(step_run__run=run).error
    assert system_queryset(Workflow).get(pk=workflow.pk).name != "Must roll back"


def test_t15_duplicate_delivery_holder_restores_busy_sibling(execution, monkeypatch):
    """A duplicate that became stale before locking still restores a busy sibling."""
    actor, sent = execution
    run = fanout(actor, "duplicate_holder")
    left, right = row(run, "left"), row(run, "right")
    pending, allow_lock, locked, release = Event(), Event(), Event(), Event()
    role = local()
    original_hold = WorkflowRunQuerySet.hold

    @contextmanager
    def hold(self, run_id, *, skip_locked=False, **kwargs):
        duplicate = getattr(role, "duplicate", False)
        if duplicate:
            pending.set()
            assert allow_lock.wait(10)
        with original_hold(self, run_id, skip_locked=skip_locked, **kwargs) as held:
            if duplicate:
                assert held is not None
                locked.set()
                assert release.wait(10)
            yield held

    def duplicate():
        role.duplicate = True
        return runner.execute(left.pk)

    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stale = pool.submit(in_connection, duplicate)
        assert pending.wait(10)
        assert runner.execute(left.pk)
        sent.clear()
        allow_lock.set()
        assert locked.wait(10)
        try:
            assert pool.submit(in_connection, runner.execute, right.pk).result(timeout=5) is False
            assert sent == []
        finally:
            release.set()
        assert stale.result(timeout=10) is False
    assert right.pk in [envelope["kwargs"]["step_run_id"] for _, envelope in sent]
    assert runner.execute(right.pk)
    assert row(run, "right").status == StepRunStatus.SUCCEEDED
    assert row(run, "right").dispatches == 0
    assert system_queryset(StepAttempt).filter(step_run=left).count() == 1


def cancel_while_database_body_runs(workflow, run, actor, monkeypatch, register_step):
    """Request cancel on another connection while the real worker holds the run lock."""
    entered, cancel_waiting, release = Event(), Event(), Event()
    original_hold = WorkflowRunQuerySet.hold
    role = local()

    @contextmanager
    def hold(self, run_id, *, skip_locked=False, **kwargs):
        if getattr(role, "cancel", False):
            cancel_waiting.set()
        with original_hold(self, run_id, skip_locked=skip_locked, **kwargs) as held:
            yield held

    def cancel():
        role.cancel = True
        WorkflowRun.objects.cancel(run, actor=actor)

    class BlockingBody(Echo):
        def run(self, ctx):
            assert ctx.step_run.node_key == "entry"
            Workflow.objects.filter(pk=workflow.pk).update(name="Committed body")
            entered.set()
            assert release.wait(10)
            return ctx.done(ctx.input)

    register_step(BlockingBody)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, runner.execute, row(run, "entry").pk)
        assert entered.wait(10)
        cancelling = pool.submit(in_connection, cancel)
        try:
            assert cancel_waiting.wait(10)
            assert not cancelling.done()
        finally:
            release.set()
        assert worker.result(timeout=10)
        cancelling.result(timeout=10)


def test_t20_cancel_waits_for_nonfinal_body_and_cancels_successor(execution, register_step, monkeypatch):
    """Cancel keeps the running step's committed work and prevents its successor's invocation."""
    actor, _ = execution
    workflow = load_workflow(document("entry", "successor"), key="cancel_nonfinal", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 20})

    cancel_while_database_body_runs(workflow, run, actor, monkeypatch, register_step)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output, retained.error) == (
        RunStatus.CANCELED, "canceled", {}, "",
    )
    entry, successor = row(run, "entry"), row(run, "successor")
    assert entry.status == StepRunStatus.SUCCEEDED and entry.output == {"value": 20}
    assert system_queryset(StepAttempt).get(step_run=entry).result == "succeeded"
    assert successor.status == StepRunStatus.CANCELED and successor.attempt == 0
    assert not system_queryset(StepAttempt).filter(step_run=successor).exists()
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Committed body"
    assert not runner.execute(successor.pk)
    assert not system_queryset(StepAttempt).filter(step_run=successor).exists()


def test_t20b_cancel_racing_final_body_preserves_success(execution, register_step, monkeypatch):
    """Final run fields survive cancellation, which leaves no open execution rows."""
    actor, _ = execution
    draft = document("entry")
    draft["results"] = [{"from": "entry", "as": "reported"}]
    workflow = load_workflow(draft, key="cancel_final", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 21})

    cancel_while_database_body_runs(workflow, run, actor, monkeypatch, register_step)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output, retained.error) == (
        RunStatus.SUCCEEDED, "reported", {"value": 21}, "",
    )
    assert row(run).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepAttempt).get(step_run=row(run)).result == "succeeded"
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Committed body"
    assert not runner.execute(row(run).pk)
    assert not system_queryset(StepRun).filter(run=run).exclude(
        status__in=StepRunStatus.terminal_values(),
    ).exists()


def test_t21_non_admin_run_as_cannot_load_another_actors_record(execution, register_step):
    """The context loader denies a foreign row under the retained run_as principal."""
    admin, _ = execution
    actor, other = create_user("run_as_reader"), create_user("foreign_owner")
    own, hidden = vault_for(actor, name="Own"), vault_for(other, name="Foreign")
    workflow = load_workflow(document("entry"), key="explicit_scope", actor=admin)
    with system_context(reason="test.workflow_actor"):
        Workflow.objects.filter(pk=workflow.pk).update(created_by=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    class ScopedRecordReader(Echo):
        def run(self, ctx):
            assert ctx.actor.pk == actor.pk and ctx.run.run_as_id == actor.pk
            assert ctx.load(type(own), own.public_id).pk == own.pk
            ctx.load(type(hidden), hidden.public_id)
            raise AssertionError("A foreign row became readable.")

    register_step(ScopedRecordReader)
    assert runner.execute(row(run).pk)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.FAILED
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.result == "failed" and "inaccessible" in attempt.error


def test_t19_database_result_error_preserves_final_body(execution, register_step, monkeypatch):
    """A PostgreSQL result-column error rolls back only planning, retaining body work."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="result_database_error", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    class RetainedBody(Echo):
        def run(self, ctx):
            Workflow.objects.filter(pk=workflow.pk).update(name="Retained final write")
            return ctx.done(ctx.input)

    register_step(RetainedBody)
    monkeypatch.setattr(Definition, "result_for", lambda *args: ("x" * 64, {}))
    assert runner.execute(row(run).pk)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.FAILED and retained.output == {} and retained.outcome == "error"
    assert "value too long" in retained.error
    assert row(run).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepAttempt).get(step_run__run=run).result == "succeeded"
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Retained final write"


def test_commit_dispatch_skips_a_ready_row_locked_by_another_claim(execution):
    """A commit-time send never waits for another holder's step-row transaction."""
    actor, sent = execution
    workflow = load_workflow(document("entry"), key="dispatch_locked_row", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    sent.clear()
    locked, release = Event(), Event()

    def claim_lock():
        with transaction.atomic(), system_context(reason="test.concurrent_claim"):
            StepRun.objects.filter(pk=step_run.pk).lock_if_supported().get()
            locked.set()
            assert release.wait(10)

    def dispatch():
        with WorkflowRun.objects.hold(run.pk):
            pass

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimant = pool.submit(in_connection, claim_lock)
        assert locked.wait(5)
        try:
            sender = pool.submit(in_connection, dispatch)
            sender.result(timeout=5)
            assert sent == []
            assert row(run).dispatched_at == step_run.dispatched_at
        finally:
            release.set()
        claimant.result(timeout=5)
    dispatch()
    assert len(sent) == 1
    assert row(run).dispatched_at > step_run.dispatched_at


class WorkerDied(BaseException):
    """Simulate abrupt worker loss outside the runner's ordinary failure handler."""


def expire_attempt(step_run):
    """Fault-inject elapsed database time on a genuinely claimed running row."""
    with system_context(reason="test expired IO deadline"):
        StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))


@pytest.mark.parametrize("effect_idempotent", [None, False, True])
def test_t3_t4_worker_loss_obeys_the_durable_effect_marker(execution, register_step, effect_idempotent):
    """Only an unmarked or provider-idempotent effect is retried without acknowledgement."""
    actor, _ = execution
    keys = []

    class LostWorker(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            assert not connection.in_atomic_block
            keys.append(ctx.idempotency_key)
            if ctx.attempt.number == 1:
                if effect_idempotent is not None:
                    ctx.begin_effect()
                raise WorkerDied()
            assert ctx.retry_acknowledged is (effect_idempotent is False)
            return ctx.done(ctx.input)

    LostWorker.effect_idempotent = bool(effect_idempotent)
    register_step(LostWorker)
    workflow = load_workflow(document("entry"), key="lost_io_worker", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(WorkerDied):
            pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
        assert row(run).status == StepRunStatus.RUNNING
        first = system_queryset(StepAttempt).get(step_run=step_run)
        assert first.finished_at is None
        assert (first.effect_started_at is not None) is (effect_idempotent is not None)
        expire_attempt(step_run)
        assert pool.submit(in_connection, runner.reap).result(timeout=10) == 1
        waiting = row(run)
        assert waiting.status == StepRunStatus.WAITING
        if effect_idempotent is False:
            assert waiting.waiting_kind == "error" and waiting.wait_reason
            assert not runner.execute(step_run.pk)
            StepRun.objects.retry_step(waiting, actor=actor, accept_duplicate=True)
        else:
            assert waiting.waiting_kind == "time" and waiting.wait_reason == ""
            assert runner.wake() == 1
        assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
    first.refresh_from_db()
    assert first.result == "timed_out"
    second = system_queryset(StepAttempt).get(step_run=step_run, number=2)
    assert second.acknowledged_by_id == (actor.pk if effect_idempotent is False else None)
    assert second.result == "succeeded" and keys[0] == keys[1]
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


@pytest.mark.parametrize("failure", ["retryable", "operational"])
def test_t4_marked_transient_failure_requires_acknowledged_redelivery(execution, register_step, failure):
    """An IO worker's durable effect marker overrides both transient failure classifications."""
    actor, _ = execution

    class MarkedTransient(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                if failure == "retryable":
                    raise Retryable("Transient after effect")
                raise OperationalError("Transient after effect") from psycopg.errors.DeadlockDetected("Transient")
            assert ctx.retry_acknowledged
            return ctx.done(ctx.input)

    register_step(MarkedTransient)
    workflow = load_workflow(document("entry"), key="marked_transient", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
        assert row(run).waiting_kind == "error"
        assert row(run).wait_reason.lower() == "possible duplicate effect"
        assert pool.submit(in_connection, runner.wake).result(timeout=10) == 0
        with pytest.raises(ValidationError, match="duplicate"):
            StepRun.objects.retry_step(step_run, actor=actor)
        StepRun.objects.retry_step(step_run, actor=actor, accept_duplicate=True)
        assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
    assert system_queryset(StepAttempt).get(step_run=step_run, number=2).acknowledged_by_id == actor.pk
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_t5_broker_failure_keeps_a_durable_ready_row(execution, monkeypatch, caplog):
    """An enqueue failure commits admission and tick delivers its retained outbox row."""
    actor, sent = execution
    workflow = load_workflow(document("entry"), key="broker_recovery", actor=actor)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Broker unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(celery_app, "send_task", unavailable)
        WorkflowRun.objects.start(workflow, actor=actor)
    assert "Broker unavailable" in caplog.text
    run = system_queryset(WorkflowRun).get(version__workflow=workflow)
    step_run = row(run)
    assert step_run.status == StepRunStatus.READY and step_run.attempt == 0
    with system_context(reason="test missed broker delivery"):
        StepRun.objects.filter(pk=step_run.pk).update(dispatched_at=Now() - timedelta(seconds=61))
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(in_connection, runner.redispatch).result(timeout=10) == 1
        assert sent[-1][1]["kwargs"]["step_run_id"] == step_run.pk
        assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_t6_reaped_io_attempt_cannot_overwrite_its_successor(execution, register_step):
    """A live but expired worker's late result loses to the next committed attempt."""
    actor, _ = execution
    entered, release = Event(), Event()

    class LateWorker(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            if ctx.attempt.number == 1:
                entered.set()
                assert release.wait(15)
            return ctx.done({"value": ctx.attempt.number})

    register_step(LateWorker)
    workflow = load_workflow(document("entry"), key="late_io_result", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stale = pool.submit(in_connection, runner.execute, step_run.pk)
        assert entered.wait(10)
        try:
            expire_attempt(step_run)
            assert pool.submit(in_connection, runner.reap).result(timeout=10) == 1
            assert runner.wake() == 1
            assert pool.submit(in_connection, runner.execute, step_run.pk).result(timeout=10)
        finally:
            release.set()
        assert stale.result(timeout=10) is False
    assert row(run).output == {"value": 2}
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 2}
    assert list(system_queryset(StepAttempt).filter(step_run=step_run).order_by("number").values_list(
        "result", flat=True,
    )) == ["timed_out", "succeeded"]


@pytest.mark.parametrize("first_holder", ["effect", "reaper"])
def test_t8_reaper_and_begin_effect_share_the_step_fence(execution, register_step, monkeypatch, first_holder):
    """Marker-first waits for an operator; reaper-first prevents the external effect."""
    actor, _ = execution
    entered, begin, locked, release, finish, effect_called, marked = (Event() for _ in range(7))
    effects = []
    role = local()
    original_update, original_close = StepAttemptQuerySet.update, StepAttemptQuerySet.close

    def update(self, **kwargs):
        if first_holder == "effect" and "effect_started_at" in kwargs:
            locked.set()
            assert release.wait(15)
        return original_update(self, **kwargs)

    def close(self, *args, **kwargs):
        if first_holder == "reaper" and getattr(role, "reaper", False):
            locked.set()
            assert release.wait(15)
        return original_close(self, *args, **kwargs)

    def reap():
        role.reaper = True
        return runner.reap()

    class EffectWorker(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            entered.set()
            assert begin.wait(15)
            effect_called.set()
            ctx.begin_effect()
            effects.append(ctx.idempotency_key)
            marked.set()
            assert finish.wait(15)
            return ctx.done(ctx.input)

    monkeypatch.setattr(StepAttemptQuerySet, "update", update)
    monkeypatch.setattr(StepAttemptQuerySet, "close", close)
    register_step(EffectWorker)
    workflow = load_workflow(document("entry"), key="effect_fence_race", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, runner.execute, step_run.pk)
        assert entered.wait(10)
        try:
            if first_holder == "effect":
                with system_context(reason="test imminent effect marker deadline"):
                    StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() + timedelta(seconds=2))
                begin.set()
                assert locked.wait(10)
                limit = monotonic() + 5
                while not system_queryset(StepRun).filter(pk=step_run.pk, deadline_at__lt=Now()).exists():
                    assert monotonic() < limit, "The database deadline did not expire."
                    sleep(0.02)
                reaper = pool.submit(in_connection, reap)
                assert reaper.result(timeout=5) == 0
                release.set()
                assert marked.wait(10)
                reaper = pool.submit(in_connection, reap)
            else:
                expire_attempt(step_run)
                reaper = pool.submit(in_connection, reap)
                assert locked.wait(10)
                begin.set()
                assert effect_called.wait(10)
            release.set()
            assert reaper.result(timeout=10) == 1
        finally:
            release.set()
            begin.set()
            finish.set()
        assert worker.result(timeout=10) is False
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == "timed_out"
    assert (attempt.effect_started_at is not None) is (first_holder == "effect")
    assert bool(effects) is (first_holder == "effect")
    assert row(run).waiting_kind == ("operator" if first_holder == "effect" else "time")


def test_t2_io_simultaneous_branch_results_plan_one_join(execution, register_step):
    """Both IO bodies overlap, then serialized settlement creates exactly one join."""
    actor, _ = execution
    run = fanout(actor, "io_parallel_join")
    barrier = Barrier(2)
    entered = Event()

    class ParallelBranch(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            assert not connection.in_atomic_block
            if ctx.step_run.node_key in {"left", "right"}:
                entered.set()
                barrier.wait(timeout=10)
            return ctx.done(ctx.input)

    register_step(ParallelBranch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, runner.execute, row(run, "left").pk)
        assert entered.wait(10), "The first IO claim must commit before delivering its sibling."
        second = pool.submit(in_connection, runner.execute, row(run, "right").pk)
        assert first.result(timeout=15) and second.result(timeout=15)
    assert system_queryset(StepRun).filter(run=run, node_key="join").count() == 1
    assert runner.execute(row(run, "join").pk)
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="join").count() == 1
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


@pytest.mark.parametrize("first_holder", ["settlement", "reaper"])
def test_t16_io_reap_and_settlement_follow_both_run_lock_orders(execution, register_step, monkeypatch, first_holder):
    """The first run-lock holder decides whether the live IO result can still settle."""
    actor, _ = execution
    entered, return_body, locked, release, settling = (Event() for _ in range(5))
    role = local()
    original_hold = WorkflowRunQuerySet.hold
    original_settle = StepRunQuerySet.settle

    @contextmanager
    def hold(self, run_id, *, skip_locked=False, **kwargs):
        current = getattr(role, "operation", None)
        if current == "settlement":
            settling.set()
        with original_hold(self, run_id, skip_locked=skip_locked, **kwargs) as retained:
            if current == first_holder == "reaper" and retained is not None:
                locked.set()
                assert release.wait(15)
            yield retained

    def settle(self, step_run, settlement, **kwargs):
        result = original_settle(self, step_run, settlement, **kwargs)
        if first_holder == "settlement":
            locked.set()
            assert release.wait(15)
        return result

    def reap():
        role.operation = "reaper"
        return runner.reap()

    class RacingSettlement(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            entered.set()
            assert return_body.wait(15)
            role.operation = "settlement"
            return ctx.done(ctx.input)

    register_step(RacingSettlement)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    monkeypatch.setattr(StepRunQuerySet, "settle", settle)
    workflow = load_workflow(document("entry"), key="io_reap_settle_race", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, runner.execute, step_run.pk)
        assert entered.wait(10)
        try:
            if first_holder == "settlement":
                with system_context(reason="test imminent IO settlement deadline"):
                    StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() + timedelta(seconds=2))
                return_body.set()
                assert locked.wait(10)
                limit = monotonic() + 5
                while not system_queryset(StepRun).filter(pk=step_run.pk, deadline_at__lt=Now()).exists():
                    assert monotonic() < limit, "The database deadline did not expire."
                    sleep(0.02)
                assert pool.submit(in_connection, reap).result(timeout=5) == 0
            else:
                expire_attempt(step_run)
                reaper = pool.submit(in_connection, reap)
                assert locked.wait(10)
                return_body.set()
                assert settling.wait(10)
                assert not worker.done()
            release.set()
            if first_holder == "reaper":
                assert reaper.result(timeout=10) == 1
        finally:
            release.set()
            return_body.set()
        assert worker.result(timeout=10) is (first_holder == "settlement")
    assert row(run).status == (StepRunStatus.SUCCEEDED if first_holder == "settlement" else StepRunStatus.WAITING)
    assert system_queryset(StepAttempt).get(step_run=step_run).result == (
        "succeeded" if first_holder == "settlement" else "timed_out"
    )


def test_cancel_lock_timeout_reports_running_and_restores_connection(execution, register_step):
    """A bounded cancel reports contention without altering the executing transaction."""
    actor, _ = execution
    entered, release = Event(), Event()

    class HeldDatabaseBody(Echo):
        def run(self, ctx):
            entered.set()
            assert release.wait(10)
            return ctx.done(ctx.input)

    register_step(HeldDatabaseBody)
    workflow = load_workflow(document("entry"), key="cancel_timeout", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    def cancel():
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            previous = cursor.fetchone()[0]
        with pytest.raises(ValidationError, match="still running"):
            WorkflowRun.objects.cancel(run, actor=actor, timeout=timedelta(milliseconds=30))
        with connection.cursor() as cursor:
            cursor.execute("SHOW lock_timeout")
            assert cursor.fetchone()[0] == previous

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, runner.execute, row(run).pk)
        assert entered.wait(10)
        try:
            pool.submit(in_connection, cancel).result(timeout=5)
            assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.RUNNING
        finally:
            release.set()
        assert worker.result(timeout=10)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_io_settlement_stops_at_deadline_when_step_row_stays_locked(execution, register_step):
    """A contended result row cannot keep an IO worker settling past its deadline."""
    actor, _ = execution
    entered, finish_body, locked, release = (Event() for _ in range(4))
    bodies = []

    class BoundedSettlement(Echo):
        mode = StepMode.IO
        timeout = timedelta(seconds=1)

        def run(self, ctx):
            bodies.append(ctx.attempt.number)
            entered.set()
            assert finish_body.wait(10)
            return ctx.done({"value": 88})

    register_step(BoundedSettlement)
    workflow = load_workflow(document("entry"), key="bounded_io_settlement", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)

    def hold_step():
        with transaction.atomic(), system_context(reason="test retained step row lock"):
            StepRun.objects.filter(pk=step_run.pk).lock_if_supported(no_key=True).get()
            locked.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, runner.execute, step_run.pk)
        assert entered.wait(10)
        holder = pool.submit(in_connection, hold_step)
        try:
            assert locked.wait(5)
            finish_body.set()
            assert worker.result(timeout=5) is False
            assert not holder.done()
        finally:
            finish_body.set()
            release.set()
        holder.result(timeout=5)
        assert pool.submit(in_connection, runner.reap).result(timeout=5) == 1
    assert bodies == [1]
    assert row(run).output == {"error": "The attempt deadline expired."}
    assert row(run).status == StepRunStatus.FAILED
    assert system_queryset(StepAttempt).get(step_run=step_run).result == "timed_out"
