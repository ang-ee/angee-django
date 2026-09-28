"""PostgreSQL races for DATABASE execution, using separate thread connections.

The run lock serializes body and settlement, so T2 cannot contain simultaneous
DATABASE settlements and T7 cannot contain a compliant concurrent superseder.
The tests name those boundaries explicitly instead of removing the run lock.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event, Lock, local

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.db.models import F
from django.db.models.functions import Now
from rebac import actor_context, system_context

from angee.base.scoping import system_queryset
from angee.jobs.enqueue import celery_app
from angee.workflows.definition import Definition
from angee.workflows.managers import WorkflowRunQuerySet
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import create_user, vault_for
from tests.workflow_steps import Echo, document

pytestmark = [
    pytest.mark.django_db(transaction=True),
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


def test_t1_duplicate_database_delivery_executes_one_body(execution, monkeypatch):
    """Duplicate DATABASE messages race the run lock and only one reaches the body."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="claim", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = row(run)
    entered, release = Event(), Event()
    invocations = []

    def body(self, ctx):
        invocations.append(ctx.attempt.number)
        entered.set()
        assert release.wait(10), "The competing worker never released the first."
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, StepRun.objects.execute, step_run.pk)
        assert entered.wait(10)
        try:
            second = pool.submit(in_connection, StepRun.objects.execute, step_run.pk)
            assert second.result(timeout=5) is False
            assert invocations == [1]
            assert row(run).status == StepRunStatus.READY  # Uncommitted claim is invisible.
        finally:
            release.set()
        assert first.result(timeout=10) is True
    assert not StepRun.objects.execute(step_run.pk)
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
    assert StepRun.objects.execute(row(run).pk)
    return run


def test_t2_serialized_branch_settlements_plan_one_join(execution, monkeypatch):
    """Deliver overlapping branches and consume only their actual commit messages."""
    actor, sent = execution
    run = fanout(actor, "join")
    sent.clear()
    entered, release = Event(), Event()

    def body(self, ctx):
        if ctx.step_run.node_key == "left":
            entered.set()
            assert release.wait(10)
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, StepRun.objects.execute, row(run, "left").pk)
        assert entered.wait(10)
        try:
            second = pool.submit(in_connection, StepRun.objects.execute, row(run, "right").pk)
            assert second.result(timeout=5) is False
            assert sent == []
        finally:
            release.set()
        assert first.result(timeout=10)
    # No table scan drives recovery: only messages sent by committed advances.
    delivered = 0
    while delivered < len(sent):
        assert delivered < 20, "Unexpected redispatch loop."
        name, envelope = sent[delivered]
        assert name == "workflows.execute"
        StepRun.objects.execute(envelope["kwargs"]["step_run_id"])
        delivered += 1
    assert row(run, "join").pk in [envelope["kwargs"]["step_run_id"] for _, envelope in sent]
    assert row(run, "join").status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepRun).filter(run=run, node_key="join").count() == 1
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="join").count() == 1
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED


def test_t15_busy_branch_is_dispatched_on_holder_commit_without_tick(execution, monkeypatch):
    """A busy sibling gets a fresh commit-time send from its successful lock holder."""
    actor, _ = execution
    run = fanout(actor, "prompt_branch")
    left, right = row(run, "left"), row(run, "right")
    entered, release = Event(), Event()
    sends, mutex = [], Lock()

    def send(name, **kwargs):
        with mutex:
            sends.append(kwargs["kwargs"]["step_run_id"])

    def body(self, ctx):
        if ctx.step_run.node_key == "left":
            entered.set()
            assert release.wait(10)
        return ctx.done(ctx.input)

    monkeypatch.setattr(celery_app, "send_task", send)
    monkeypatch.setattr(Echo, "run", body)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(in_connection, StepRun.objects.execute, left.pk)
        assert entered.wait(10)
        try:
            dropped = pool.submit(in_connection, StepRun.objects.execute, right.pk)
            assert dropped.result(timeout=5) is False
            assert sends == []
        finally:
            release.set()
        assert first.result(timeout=10)
        assert right.pk in sends, "Holder commit must restore the busy branch's delivery."
        second = pool.submit(in_connection, StepRun.objects.execute, right.pk)
        assert second.result(timeout=5)
    assert row(run, "right").status == StepRunStatus.SUCCEEDED
    assert row(run, "right").dispatches == 0
    assert row(run, "join").status == StepRunStatus.READY


def test_t7_reentrant_fence_fault_rolls_back_database_body(execution, monkeypatch):
    """Inject reentrant invalidation; compliant concurrent invalidation cannot occur."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="fence", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    original = workflow.name
    entered, release = Event(), Event()

    def body(self, ctx):
        Workflow.objects.filter(pk=workflow.pk).update(name="Uncommitted domain write")
        with system_context(reason="test.inject_stale_fence"):
            StepRun.objects.filter(pk=ctx.step_run.pk).update(attempt=F("attempt") + 1)
        entered.set()
        assert release.wait(10)
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(in_connection, StepRun.objects.execute, row(run).pk)
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
def test_t16_redispatch_and_settlement_follow_both_lock_orders(execution, monkeypatch, first_holder):
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
    def hold(self, run_id, *, skip_locked=False):
        with original_hold(self, run_id, skip_locked=skip_locked) as held:
            if getattr(role, "tick", False) and held is not None:
                entered.set()
                assert release.wait(10)
            yield held

    def tick():
        role.tick = True
        return StepRun.objects.redispatch()

    def body(self, ctx):
        if first_holder == "worker":
            entered.set()
            assert release.wait(10)
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        action = tick if first_holder == "tick" else lambda: StepRun.objects.execute(step_run.pk)
        first = pool.submit(in_connection, action)
        assert entered.wait(10)
        try:
            if first_holder == "worker":
                assert system_queryset(StepRun).filter(pk=step_run.pk, status="ready").exists()
                assert pool.submit(in_connection, StepRun.objects.redispatch).result(timeout=5) == 0
            else:
                assert pool.submit(in_connection, StepRun.objects.execute, step_run.pk).result(timeout=5) is False
        finally:
            release.set()
        assert first.result(timeout=10)
    if first_holder == "tick":
        assert sent[-1][1]["kwargs"]["step_run_id"] == step_run.pk
        assert StepRun.objects.execute(step_run.pk)
    assert StepRun.objects.redispatch() == 0
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
        assert pool.submit(in_connection, StepRun.objects.execute, row(run).pk).result(timeout=10)
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 1}
    with actor_context(actor):
        assert type(own).objects.filter(pk=own.pk).exists()
        assert not type(hidden).objects.filter(pk=hidden.pk).exists()


def test_statement_timeout_rolls_back_body_and_restores_connection(execution, monkeypatch):
    """A blocked domain write times out inside its savepoint and closes the attempt."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="timeout", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    monkeypatch.setattr(Echo, "timeout", timedelta(milliseconds=100))

    def body(self, ctx):
        Workflow.objects.filter(pk=workflow.pk).update(name="Must roll back")
        return ctx.done(ctx.input)

    def execute_and_read_timeout(pk):
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            before = cursor.fetchone()[0]
        executed = StepRun.objects.execute(pk)
        with connection.cursor() as cursor:
            cursor.execute("SHOW statement_timeout")
            assert cursor.fetchone()[0] == before
        return executed

    monkeypatch.setattr(Echo, "run", body)
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
    def hold(self, run_id, *, skip_locked=False):
        duplicate = getattr(role, "duplicate", False)
        if duplicate:
            pending.set()
            assert allow_lock.wait(10)
        with original_hold(self, run_id, skip_locked=skip_locked) as held:
            if duplicate:
                assert held is not None
                locked.set()
                assert release.wait(10)
            yield held

    def duplicate():
        role.duplicate = True
        return StepRun.objects.execute(left.pk)

    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        stale = pool.submit(in_connection, duplicate)
        assert pending.wait(10)
        assert StepRun.objects.execute(left.pk)
        sent.clear()
        allow_lock.set()
        assert locked.wait(10)
        try:
            assert pool.submit(in_connection, StepRun.objects.execute, right.pk).result(timeout=5) is False
            assert sent == []
        finally:
            release.set()
        assert stale.result(timeout=10) is False
    assert right.pk in [envelope["kwargs"]["step_run_id"] for _, envelope in sent]
    assert StepRun.objects.execute(right.pk)
    assert row(run, "right").status == StepRunStatus.SUCCEEDED
    assert row(run, "right").dispatches == 0
    assert system_queryset(StepAttempt).filter(step_run=left).count() == 1


def cancel_while_database_body_runs(workflow, run, actor, monkeypatch):
    """Request cancel on another connection while the real worker holds the run lock."""
    entered, cancel_waiting, release = Event(), Event(), Event()
    original_hold = WorkflowRunQuerySet.hold
    role = local()

    @contextmanager
    def hold(self, run_id, *, skip_locked=False):
        if getattr(role, "cancel", False):
            cancel_waiting.set()
        with original_hold(self, run_id, skip_locked=skip_locked) as held:
            yield held

    def cancel():
        role.cancel = True
        WorkflowRun.objects.cancel(run, actor=actor)

    def body(self, ctx):
        assert ctx.step_run.node_key == "entry"
        Workflow.objects.filter(pk=workflow.pk).update(name="Committed body")
        entered.set()
        assert release.wait(10)
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(in_connection, StepRun.objects.execute, row(run, "entry").pk)
        assert entered.wait(10)
        cancelling = pool.submit(in_connection, cancel)
        try:
            assert cancel_waiting.wait(10)
            assert not cancelling.done()
        finally:
            release.set()
        assert worker.result(timeout=10)
        cancelling.result(timeout=10)


def test_t20_cancel_waits_for_nonfinal_body_and_cancels_successor(execution, monkeypatch):
    """Cancel keeps the running step's committed work and prevents its successor's invocation."""
    actor, _ = execution
    workflow = load_workflow(document("entry", "successor"), key="cancel_nonfinal", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 20})

    cancel_while_database_body_runs(workflow, run, actor, monkeypatch)

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
    assert not StepRun.objects.execute(successor.pk)
    assert not system_queryset(StepAttempt).filter(step_run=successor).exists()


def test_t20b_cancel_racing_final_body_preserves_success(execution, monkeypatch):
    """A final settlement wins before cancel acquires the lock and keeps its complete result."""
    actor, _ = execution
    draft = document("entry")
    draft["results"] = [{"from": "entry", "as": "reported"}]
    workflow = load_workflow(draft, key="cancel_final", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 21})

    cancel_while_database_body_runs(workflow, run, actor, monkeypatch)

    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert (retained.status, retained.outcome, retained.output, retained.error) == (
        RunStatus.SUCCEEDED, "reported", {"value": 21}, "",
    )
    assert row(run).status == StepRunStatus.SUCCEEDED
    assert system_queryset(StepAttempt).get(step_run=row(run)).result == "succeeded"
    assert system_queryset(Workflow).get(pk=workflow.pk).name == "Committed body"
    assert not StepRun.objects.execute(row(run).pk)


def test_t21_non_admin_run_as_cannot_load_another_actors_record(execution, monkeypatch):
    """The context loader denies a foreign row under the retained run_as principal."""
    admin, _ = execution
    actor, other = create_user("run_as_reader"), create_user("foreign_owner")
    own, hidden = vault_for(actor, name="Own"), vault_for(other, name="Foreign")
    workflow = load_workflow(document("entry"), key="explicit_scope", actor=admin)
    with system_context(reason="test.workflow_actor"):
        Workflow.objects.filter(pk=workflow.pk).update(created_by=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    def body(self, ctx):
        assert ctx.actor.pk == actor.pk and ctx.run.run_as_id == actor.pk
        assert ctx.load(type(own), own.public_id).pk == own.pk
        ctx.load(type(hidden), hidden.public_id)
        raise AssertionError("A foreign row became readable.")

    monkeypatch.setattr(Echo, "run", body)
    assert StepRun.objects.execute(row(run).pk)
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.FAILED
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.result == "failed" and "inaccessible" in attempt.error


def test_t19_database_result_error_preserves_final_body(execution, monkeypatch):
    """A PostgreSQL result-column error rolls back only planning, retaining body work."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="result_database_error", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    def body(self, ctx):
        Workflow.objects.filter(pk=workflow.pk).update(name="Retained final write")
        return ctx.done(ctx.input)

    monkeypatch.setattr(Echo, "run", body)
    monkeypatch.setattr(Definition, "result_for", lambda *args: ("x" * 64, {}))
    assert StepRun.objects.execute(row(run).pk)
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
