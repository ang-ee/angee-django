"""IO execution, retries, operator recovery and retained evidence contracts."""

from contextlib import contextmanager
from datetime import timedelta

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DataError, OperationalError, connection, transaction
from django.db.models.functions import Now
from rebac import actor_context, current_actor, system_context, to_subject_ref
from rebac.actors import is_sudo

from angee.base.scoping import system_queryset
from angee.workflows import runner as runner_module
from angee.workflows.managers import StepRunQuerySet
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import Retryable, RetryPolicy, StepMode, Superseded
from angee.workflows.testing.drivers import load_workflow, register_steps, run_until
from angee.workflows.testing.models import StepArtifact, StepAttempt, StepRun, Workflow, WorkflowRun
from tests.conftest import create_user, vault_for
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def step_row(run):
    """Fetch the retained row without imposing a requester on the step body."""
    return system_queryset(StepRun).get(run=run)


def test_io_body_is_outside_transactions_and_scoped_to_its_actor(execution, register_step):
    """The committed claim supplies a non-admin principal to ordinary ORM reads."""
    admin, _ = execution
    actor, outsider = create_user("io_reader"), create_user("io_outsider")
    own, hidden = vault_for(actor), vault_for(outsider)

    class ScopedIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            assert not connection.in_atomic_block and not is_sudo()
            assert current_actor() == to_subject_ref(actor)
            assert ctx.now == ctx.attempt.started_at
            assert ctx.step_run.deadline_at > ctx.now
            assert list(type(own).objects.values_list("pk", flat=True)) == [own.pk]
            assert not type(hidden).objects.filter(pk=hidden.pk).exists()
            return ctx.done({"value": 1})

    register_step(ScopedIO)
    workflow = load_workflow(document("entry"), key="scoped_io", actor=admin)
    workflow.grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 1}


def test_io_refuses_an_enclosing_transaction(execution, register_step):
    """A caller cannot accidentally hold its own transaction over an IO body."""
    actor, _ = execution
    called = []

    class IndependentIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            called.append(True)
            return ctx.done(ctx.input)

    register_step(IndependentIO)
    workflow = load_workflow(document("entry"), key="enclosing_io", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    with pytest.raises(RuntimeError, match="enclosing transaction"), transaction.atomic():
        runner.execute(step_row(run).pk)
    assert called == []
    assert step_row(run).status == StepRunStatus.READY
    assert not system_queryset(StepAttempt).filter(step_run__run=run).exists()


def test_heartbeat_extends_deadline_and_effect_marker_is_written_once(execution, register_step):
    """Context mutations retain their fence and never leave the IO body in a transaction."""
    actor, _ = execution

    class HeartbeatIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            old_deadline = ctx.step_run.deadline_at
            with system_context(reason="test shorten live IO deadline"):
                StepRun.objects.filter(pk=ctx.step_run.pk).update(deadline_at=Now() + timedelta(seconds=1))
            ctx.heartbeat()
            assert ctx.step_run.deadline_at >= old_deadline
            ctx.begin_effect()
            first = system_queryset(StepAttempt).get(pk=ctx.attempt.pk).effect_started_at
            assert first is not None
            ctx.begin_effect()
            assert system_queryset(StepAttempt).get(pk=ctx.attempt.pk).effect_started_at == first
            ctx.raise_if_canceled()
            assert not connection.in_atomic_block and not is_sudo()
            return ctx.done(ctx.input)

    register_step(HeartbeatIO)
    workflow = load_workflow(document("entry"), key="heartbeat_io", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    assert step_row(run).status == StepRunStatus.SUCCEEDED


def test_cancel_fences_every_io_context_write(execution, register_step):
    """Cooperative cancellation prevents markers, heartbeats and a late successful result."""
    actor, _ = execution
    stopped = []

    class CanceledIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            WorkflowRun.objects.cancel(ctx.run, actor=ctx.actor)
            for operation in (ctx.begin_effect, ctx.heartbeat, ctx.raise_if_canceled):
                with pytest.raises(Superseded):
                    operation()
                stopped.append(operation.__name__)
            return ctx.done({"value": 99})

    register_step(CanceledIO)
    workflow = load_workflow(document("entry"), key="cancel_io", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk) is False
    assert len(stopped) == 3
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.CANCELED
    assert step_row(run).output == {}
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.effect_started_at is None and attempt.result == "superseded"


@pytest.mark.parametrize("mode", [StepMode.DATABASE, StepMode.IO])
def test_paging_retains_each_pages_random_idempotency_key_across_retries(execution, register_step, mode):
    """The first claim generates a random token; completed pages advance its suffix."""
    actor, _ = execution
    observed = []

    class Paging(Echo):
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            observed.append((ctx.attempt.number, ctx.is_last_attempt, ctx.idempotency_key, ctx.state))
            if ctx.attempt.number in {1, 3}:
                raise Retryable("Transient page failure")
            if ctx.attempt.number == 2:
                return ctx.next_page({"page": 2})
            return ctx.done(ctx.input)

    Paging.mode = mode
    register_step(Paging)
    workflow = load_workflow(document("entry"), key="retry_pages", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = step_row(run)
    assert step_run.idempotency_token is None and step_run.page_index == 0
    for number in range(1, 5):
        assert runner.execute(step_run.pk)
        retained = step_row(run)
        if number in {1, 3}:
            assert retained.waiting_kind == "time" and retained.retries == 1
            assert runner.wake() == 1
        else:
            assert retained.retries == 0
    assert [item[0:2] for item in observed] == [(1, False), (2, True), (3, False), (4, True)]
    retained = step_row(run)
    assert retained.idempotency_token.version == 4
    assert [item[2] for item in observed] == [
        f"{retained.idempotency_token}:0", f"{retained.idempotency_token}:0",
        f"{retained.idempotency_token}:1", f"{retained.idempotency_token}:1",
    ]
    assert retained.page_index == 1
    assert list(system_queryset(StepAttempt).filter(step_run=retained).order_by("number").values_list(
        "page_index", flat=True,
    )) == [0, 0, 1, 1]
    assert observed[2][3] == {"page": 2}
    assert retained.status == StepRunStatus.SUCCEEDED
    another = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(another).pk)
    assert step_row(another).idempotency_token != retained.idempotency_token


def test_time_wait_keeps_the_current_page_retry_allowance(execution, register_step):
    """Successful waits do not erase preceding failed attempts on the same page."""
    actor, _ = execution
    observed = []

    class WaitingIO(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            observed.append(ctx.is_last_attempt)
            if ctx.attempt.number == 1:
                raise Retryable("Transient failure before waiting")
            if ctx.attempt.number == 2:
                return ctx.wait(until=ctx.now - timedelta(seconds=1), state={"resumed": True})
            assert ctx.state == {"resumed": True}
            return ctx.done(ctx.input)

    register_step(WaitingIO)
    workflow = load_workflow(document("entry"), key="wait_retry_budget", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    for _ in range(2):
        assert runner.execute(step_row(run).pk)
        assert step_row(run).retries == 1
        assert runner.wake() == 1
    assert runner.execute(step_row(run).pk)
    assert observed == [False, True, True]
    assert step_row(run).retries == 0


def test_io_retry_exhaustion_is_owned_by_the_failed_attempt(execution, register_step):
    """Retryable failures consume the policy and leave no duplicate run error."""
    actor, _ = execution

    class ExhaustedIO(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            raise Retryable("Still unavailable")

    register_step(ExhaustedIO)
    workflow = load_workflow(document("entry"), key="io_retry_exhausted", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    assert runner.wake() == 1
    assert runner.execute(step_row(run).pk)
    assert step_row(run).status == StepRunStatus.FAILED
    assert step_row(run).retries == 2
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.FAILED and retained.error == ""
    assert system_queryset(StepAttempt).filter(step_run__run=run, result="failed").count() == 2


class WorkerLost(BaseException):
    """Leave a real IO claim unfinished as a process crash would."""


@pytest.mark.parametrize("explicit_actor", [False, True])
def test_operator_retry_records_duplicate_acceptance_on_only_the_new_attempt(execution, register_step, explicit_actor):
    """Unsafe retry requires explicit consent, retaining the requesting actor's identity."""
    actor, _ = execution
    acknowledgements = []

    class EffectIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            acknowledgements.append(ctx.retry_acknowledged)
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                raise WorkerLost()
            return ctx.done(ctx.input)

    register_step(EffectIO)
    workflow = load_workflow(document("entry"), key="operator_effect_retry", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step_run = step_row(run)
    with pytest.raises(WorkerLost):
        runner.execute(step_run.pk)
    with system_context(reason="test expired unsafe IO attempt"):
        StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))
    assert runner.reap() == 1
    waiting = step_row(run)
    assert waiting.waiting_kind == "operator" and waiting.wait_reason
    with pytest.raises(ValidationError, match="duplicate"):
        StepRun.objects.retry_step(waiting, actor=actor)
    with pytest.raises(PermissionDenied):
        StepRun.objects.retry_step(waiting, actor=create_user("retry_outsider"), accept_duplicate=True)
    with system_context(reason="test duplicate acknowledgement needs attribution"):
        with pytest.raises(PermissionDenied, match="requires an actor"):
            StepRun.objects.retry_step(waiting, accept_duplicate=True)
    with actor_context(actor):
        ready = StepRun.objects.retry_step(waiting, actor=actor if explicit_actor else None, accept_duplicate=True)
    assert ready.status == StepRunStatus.READY and ready.wait_reason == ""
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.RUNNING and retained.finished_at is None and retained.error == ""
    assert runner.execute(step_run.pk)
    first, second = list(system_queryset(StepAttempt).filter(step_run=step_run).order_by("number"))
    assert first.acknowledged_by_id is None and second.acknowledged_by_id == actor.pk
    assert acknowledgements == [False, True]
    assert step_row(run).retry_acknowledged_by_id is None


def test_operator_retry_keeps_successful_predecessors(execution, register_step):
    """Retrying a failed node clears terminal run fields and keeps completed work."""
    actor, _ = execution
    calls = []

    class FailOnce(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            calls.append(ctx.step_run.node_key)
            if ctx.step_run.node_key == "last" and ctx.attempt.number == 1:
                return ctx.fail("Retry this node")
            return ctx.done(ctx.input)

    register_step(FailOnce)
    workflow = load_workflow(document("first", "last"), key="retain_predecessor", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 7})
    run_until(run)
    failed = system_queryset(StepRun).get(run=run, node_key="last")
    StepRun.objects.retry_step(failed, actor=actor)
    assert runner.execute(failed.pk)
    assert calls == ["first", "last", "last"]
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 7}


def test_operator_cannot_retry_a_routed_failure(execution, register_step):
    """An error edge that already advanced the graph cannot be replayed in place."""
    actor, _ = execution
    workflow = load_workflow({"nodes": {
        "entry": {"step": "reject", "next": {"error": "recover"}},
        "recover": {"step": "echo", "input": {"value": {"value": 1}}},
    }}, key="routed_retry", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    failed = step_row(run)
    assert runner.execute(failed.pk)
    with pytest.raises(ValidationError, match="cannot be retried"):
        StepRun.objects.retry_step(failed, actor=actor)


def test_operator_wait_can_retry_even_when_the_node_declares_an_error_edge(execution, settings):
    """Infrastructure waits never took the error edge and remain retryable in place."""
    actor, _ = execution
    settings.ANGEE_WORKFLOW_MAX_DISPATCHES = 1
    workflow = load_workflow({"nodes": {
        "entry": {"step": "echo", "next": {"error": "recover"}},
        "recover": {"step": "echo", "input": {"value": {"value": 1}}},
    }}, key="unrouted_operator_wait", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    row = step_row(run)
    with system_context(reason="test operator wait after lost delivery"):
        StepRun.objects.filter(pk=row.pk).update(dispatched_at=Now() - timedelta(seconds=61))
    assert runner.redispatch() == 1
    assert step_row(run).waiting_kind == "operator"

    ready = StepRun.objects.retry_step(row, actor=actor)

    assert ready.status == StepRunStatus.READY and ready.wait_reason == ""
    assert runner.execute(row.pk)
    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.SUCCEEDED
    assert not system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="recover").exists()


@pytest.mark.parametrize("readable", [False, True])
def test_artifact_requires_record_read_access_and_retains_shared_reference(execution, register_step, readable):
    """Attaching evidence grants no access to an otherwise private target record."""
    admin, _ = execution
    actor, outsider = create_user("artifact_reader"), create_user("artifact_owner")
    record = vault_for(actor if readable else outsider)

    class ArtifactIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            ctx.artifact(record, "Evidence")
            return ctx.done(ctx.input)

    register_step(ArtifactIO)
    workflow = load_workflow(document("entry"), key="io_artifact", actor=admin)
    workflow.grant_record_access("starter", actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    artifacts = StepArtifact.objects.with_actor(actor).filter(step_run__run=run)
    if readable:
        artifact = artifacts.get()
        assert artifact.label == "Evidence" and artifact.record_ref.public_id == record.public_id
        assert artifact.record_ref.model_label == record._meta.label
        assert step_row(run).status == StepRunStatus.SUCCEEDED
    else:
        assert not artifacts.exists()
        assert step_row(run).status == StepRunStatus.FAILED
        assert "Read access" in system_queryset(StepAttempt).get(step_run__run=run).error


def test_reprocess_retains_lineage_and_uses_the_current_publication(execution):
    """Reprocessing names the replaced terminal run without reusing its request key."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="reprocess_lineage", actor=actor)
    original = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 4}, request_key="test:lineage")
    run_until(original)
    Workflow.objects.save_draft(
        workflow, draft=document("entry", "last"), expected_revision=workflow.draft_revision, actor=actor,
    )
    current = Workflow.objects.publish(workflow, actor=actor)
    replacement = WorkflowRun.objects.reprocess(original, actor=actor)
    assert original.origin == "manual" and replacement.origin == "reprocess"
    assert replacement.reprocess_of_id == original.pk and replacement.version_id == current.pk
    assert replacement.input == original.input and replacement.request_key is None


def test_io_settlement_retries_database_failure_without_repeating_body(execution, register_step, monkeypatch):
    """A transient result write retries its transaction and retains one claimed attempt."""
    actor, _ = execution
    bodies, settlements, delays = [], [], []
    original = StepRunQuerySet.settle

    class DurableResult(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            bodies.append(ctx.attempt.number)
            return ctx.done({"value": 42})

    def settle(self, step_run, settlement):
        settlements.append(step_run.attempt)
        if len(settlements) == 1:
            raise OperationalError("Transient settlement connection failure")
        return original(self, step_run, settlement)

    register_step(DurableResult)
    monkeypatch.setattr(runner_module.time, "sleep", delays.append)
    monkeypatch.setattr(StepRunQuerySet, "settle", settle)
    workflow = load_workflow(document("entry"), key="settlement_retry", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    assert bodies == [1] and settlements == [1, 1]
    assert len(delays) == 1 and delays[0] > 0
    assert system_queryset(WorkflowRun).get(pk=run.pk).output == {"value": 42}
    assert system_queryset(StepAttempt).filter(step_run__run=run).count() == 1


def test_io_soft_limit_records_a_timed_out_attempt(execution, register_step):
    """The worker's soft time limit keeps its real cause on the attempt."""
    actor, _ = execution

    class LimitedIO(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            raise SoftTimeLimitExceeded()

    register_step(LimitedIO)
    workflow = load_workflow(document("entry"), key="io_soft_limit", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(step_row(run).pk)
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.result == "timed_out" and "SoftTimeLimitExceeded" in attempt.stacktrace
    assert system_queryset(WorkflowRun).get(pk=run.pk).error == ""


def test_reaper_retains_missing_implementation_cause_and_requests_operator_help(execution):
    """An expired claim whose class disappeared cannot remain running indefinitely."""
    actor, _ = execution

    class RemovedIO(Echo):
        key = "removed_io_step"
        mode = StepMode.IO

        def run(self, ctx):
            raise WorkerLost()

    with register_steps(RemovedIO):
        workflow = load_workflow(document("entry", step=RemovedIO.key), key="removed_io", actor=actor)
        run = WorkflowRun.objects.start(workflow, actor=actor)
        step_run = step_row(run)
        with pytest.raises(WorkerLost):
            runner.execute(step_run.pk)
    with system_context(reason="test expired removed implementation"):
        StepRun.objects.filter(pk=step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))

    assert runner.reap() == 1

    waiting = step_row(run)
    assert waiting.status == StepRunStatus.WAITING and waiting.waiting_kind == "operator"
    assert "registration" in waiting.wait_reason
    attempt = system_queryset(StepAttempt).get(step_run=step_run)
    assert attempt.result == "timed_out" and RemovedIO.key in attempt.error
    assert attempt.error != waiting.wait_reason
    retained = system_queryset(WorkflowRun).get(pk=run.pk)
    assert retained.status == RunStatus.WAITING and retained.error == ""


def test_soft_limit_during_settlement_lock_wait_is_recorded_as_timeout(execution, register_step, monkeypatch):
    """The soft limit is handled while waiting for the result lock as well as inside the body."""
    actor, _ = execution
    original = StepRunQuerySet.settle
    original_fenced = runner_module.Runner._fenced
    bodies, settlements, waits = [], [], []

    class InterruptedSettlement(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            bodies.append(ctx.attempt.number)
            return ctx.done(ctx.input)

    def settle(self, step_run, settlement):
        settlements.append(settlement)
        if len(settlements) == 1:
            raise OperationalError("Retry the result transaction")
        return original(self, step_run, settlement)

    @contextmanager
    def interrupted_fence(self, step_run):
        waits.append(step_run.pk)
        if len(waits) == 2:
            raise SoftTimeLimitExceeded()
        with original_fenced(self, step_run) as current:
            yield current

    register_step(InterruptedSettlement)
    monkeypatch.setattr(StepRunQuerySet, "settle", settle)
    monkeypatch.setattr(runner_module.Runner, "_fenced", interrupted_fence)
    workflow = load_workflow(document("entry"), key="settlement_soft_limit", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)

    assert runner.execute(step_row(run).pk)

    assert bodies == [1] and len(settlements) == 2
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.result == "timed_out" and "SoftTimeLimitExceeded" in attempt.stacktrace
    assert step_row(run).status == StepRunStatus.FAILED


def test_cancel_accepts_a_pinned_requester_without_ambient_scope(execution):
    """The requester's resolved identity survives replacing the run with its locked row."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="pinned_cancel", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    pinned = WorkflowRun._base_manager.get(pk=run.pk).with_actor(actor)

    WorkflowRun.objects.cancel(pinned)

    assert system_queryset(WorkflowRun).get(pk=run.pk).status == RunStatus.CANCELED


class RecoverableBranch(Echo):
    """Fail the first delivery so retry can resume a real fork."""

    key = "recoverable_branch"

    def run(self, ctx):
        if ctx.attempt.number == 1:
            return ctx.fail("Retry this branch")
        return ctx.done(ctx.input)


def recovery_fork(sibling_step):
    """Declare two branches whose join consumes the preserved sibling's result."""

    return {
        "nodes": {
            "entry": {"step": "echo", "next": {"done": ["failed", "sibling"]}},
            "failed": {"step": RecoverableBranch.key, "next": {"done": "join"}},
            "sibling": {"step": sibling_step, "next": {"done": "join"}},
            "join": {"step": "echo", "input": {"value": {"from": "sibling", "path": ["value"]}}},
        },
        "results": [{"from": "join"}],
    }


@pytest.mark.parametrize("sibling_status", [StepRunStatus.READY, StepRunStatus.WAITING])
def test_failed_fork_preserves_open_siblings_until_retry(execution, register_step, run_factory, sibling_status):
    """Terminal delivery and tick are inert; retry resumes preserved work and its join."""

    actor, sent = execution
    register_step(RecoverableBranch)
    workflow = load_workflow(
        recovery_fork("pause" if sibling_status == StepRunStatus.WAITING else "echo"),
        key="preserved_fork", actor=actor,
    )
    run = run_factory(workflow, actor=actor).at("failed", input={"value": 17})
    failed = system_queryset(StepRun).get(run=run, node_key="failed")
    sibling = system_queryset(StepRun).get(run=run, node_key="sibling")
    if sibling_status == StepRunStatus.WAITING:
        assert runner.execute(sibling.pk)
    sibling.refresh_from_db()
    preserved = (sibling.status, sibling.state, sibling.wake_at, sibling.attempt, sibling.retries)
    deliveries_before_failure = len(sent)

    assert runner.execute(failed.pk)

    terminal = system_queryset(WorkflowRun).get(pk=run.pk)
    assert terminal.status == RunStatus.FAILED
    assert sent[deliveries_before_failure:] == [
        ("workflows.wake_run", {"kwargs": {"run_id": run.pk}, "queue": None, "expires": None}),
    ]
    assert runner.execute(sibling.pk) is False
    assert runner.tick() == {
        "woken": 0, "reaped": 0, "redispatched": 0, "decisions": 0, "runs": 0, "records": 0,
        "pruned": 0, "drained": 0,
    }
    sibling.refresh_from_db()
    assert (sibling.status, sibling.state, sibling.wake_at, sibling.attempt, sibling.retries) == preserved
    assert not system_queryset(StepRun).filter(run=run, node_key="join").exists()

    StepRun.objects.retry_step(failed, actor=actor)

    resumed = system_queryset(WorkflowRun).get(pk=run.pk)
    assert resumed.status == RunStatus.RUNNING
    assert resumed.finished_at is None and resumed.outcome == "" and resumed.error == ""
    if sibling_status == StepRunStatus.WAITING:
        assert runner.wake() == 1
    run_until(run)
    completed = system_queryset(WorkflowRun).get(pk=run.pk)
    assert completed.status == RunStatus.SUCCEEDED and completed.output == {"value": 17}
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="join").count() == 1
    assert system_queryset(StepAttempt).filter(step_run=sibling).count() == (
        2 if sibling_status == StepRunStatus.WAITING else 1
    )


@pytest.mark.parametrize("publication_failure", [False, True])
def test_running_io_sibling_records_result_on_failed_run_without_planning(
    execution, register_step, run_factory, monkeypatch, publication_failure,
):
    """A committed IO attempt retains its result; retry later plans the preserved join."""

    actor, sent = execution
    observed = {}
    original_publish = runner_module.publish_change

    def failed_publication(*args, **kwargs):
        raise RuntimeError("Result publication unavailable")

    class FinishingSibling(Echo):
        key = "finishing_sibling"
        mode = StepMode.IO

        def run(self, ctx):
            assert not connection.in_atomic_block
            failed = system_queryset(StepRun).get(run=ctx.run, node_key="failed")
            assert runner.execute(failed.pk)
            observed["terminal"] = system_queryset(WorkflowRun).filter(pk=ctx.run.pk).values(
                "status", "finished_at", "outcome", "output", "error",
            ).get()
            observed["deliveries"] = len(sent)
            assert observed["terminal"]["status"] == RunStatus.FAILED
            ctx.raise_if_canceled()
            ctx.heartbeat()
            ctx.begin_effect()
            if publication_failure:
                monkeypatch.setattr(runner_module, "publish_change", failed_publication)
            return ctx.done({"value": 41})

    register_step(RecoverableBranch)
    register_step(FinishingSibling)
    workflow = load_workflow(recovery_fork(FinishingSibling.key), key="finishing_fork", actor=actor)
    run = run_factory(workflow, actor=actor).at("sibling")
    sibling = system_queryset(StepRun).get(run=run, node_key="sibling")

    assert runner.execute(sibling.pk)

    sibling.refresh_from_db()
    assert sibling.status == StepRunStatus.SUCCEEDED and sibling.output == {"value": 41}
    attempt = system_queryset(StepAttempt).get(step_run=sibling)
    assert attempt.result == "succeeded" and attempt.finished_at is not None
    assert attempt.effect_started_at is not None
    assert system_queryset(WorkflowRun).filter(pk=run.pk).values(*observed["terminal"]).get() == observed["terminal"]
    assert len(sent) == observed["deliveries"]
    assert not system_queryset(StepRun).filter(run=run, node_key="join").exists()

    monkeypatch.setattr(runner_module, "publish_change", original_publish)
    failed = system_queryset(StepRun).get(run=run, node_key="failed")
    StepRun.objects.retry_step(failed, actor=actor)
    run_until(run)

    completed = system_queryset(WorkflowRun).get(pk=run.pk)
    assert completed.status == RunStatus.SUCCEEDED and completed.output == {"value": 41}
    assert system_queryset(StepAttempt).filter(step_run=sibling).count() == 1
    assert system_queryset(StepAttempt).filter(step_run__run=run, step_run__node_key="join").count() == 1


def test_late_io_result_data_error_stays_on_its_attempt(execution, register_step, run_factory, monkeypatch):
    """A result rejected by persistence cannot erase the original terminal cause."""

    actor, _ = execution
    observed, settlements = {}, []
    original_settle = StepRunQuerySet.settle

    class RejectedResult(Echo):
        key = "rejected_result"
        mode = StepMode.IO

        def run(self, ctx):
            failed = system_queryset(StepRun).get(run=ctx.run, node_key="failed")
            assert runner.execute(failed.pk)
            observed.update(system_queryset(WorkflowRun).filter(pk=ctx.run.pk).values(
                "status", "finished_at", "outcome", "output", "error",
            ).get())
            return ctx.done({"value": 23})

    def reject_result_once(self, step_run, settlement):
        if step_run.node_key == "sibling":
            settlements.append(type(settlement.settlement).__name__.lower())
            if settlements == ["done"]:
                raise DataError("Result contains an invalid JSON value")
        return original_settle(self, step_run, settlement)

    register_step(RecoverableBranch)
    register_step(RejectedResult)
    workflow = load_workflow(recovery_fork(RejectedResult.key), key="late_result_error", actor=actor)
    run = run_factory(workflow, actor=actor).at("sibling")
    sibling = system_queryset(StepRun).get(run=run, node_key="sibling")
    monkeypatch.setattr(StepRunQuerySet, "settle", reject_result_once)

    assert runner.execute(sibling.pk)

    sibling.refresh_from_db()
    assert settlements == ["done", "fail"]
    assert sibling.status == StepRunStatus.FAILED and sibling.outcome == "error" and sibling.output == {}
    attempt = system_queryset(StepAttempt).get(step_run=sibling)
    assert attempt.result == "failed" and attempt.finished_at is not None
    assert attempt.error == "DataError: Result contains an invalid JSON value"
    assert "DataError" in attempt.stacktrace
    assert observed["status"] == RunStatus.FAILED
    assert system_queryset(WorkflowRun).filter(pk=run.pk).values(*observed).get() == observed
    assert not system_queryset(StepRun).filter(run=run, node_key="join").exists()
