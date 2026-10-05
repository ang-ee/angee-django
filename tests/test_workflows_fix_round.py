"""Regression proofs for settlement recovery, effect acknowledgement and terminal cleanup."""

from contextlib import contextmanager
from datetime import timedelta

import psycopg
import pytest
from django.core.exceptions import ValidationError
from django.db import DataError, OperationalError, connection
from django.db.models.functions import Now
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.workflows import managers
from angee.workflows import runner as runner_module
from angee.workflows.managers import StepRunQuerySet, WorkflowRunQuerySet
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import Retryable, RetryPolicy, Step, StepMode, Superseded
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import StepAttempt, StepRecord, StepRun, Workflow, WorkflowRun
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def retained_step(run, key="entry"):
    """Read the actual execution row without changing the body's requester."""
    return system_queryset(StepRun).get(run=run, node_key=key)


def terminal_fields(run):
    """Observe the run fields that terminal cleanup must leave immutable."""
    return system_queryset(WorkflowRun).filter(pk=run.pk).values(
        "status", "outcome", "output", "error", "finished_at",
    ).get()


def fork(sibling_step="echo"):
    """Admit two real sibling rows after a successful entry settlement."""
    return {
        "nodes": {
            "entry": {"step": "echo", "next": {"done": ["failed", "sibling"]}},
            "failed": {"step": "reject", "next": {"done": "join"}},
            "sibling": {"step": sibling_step, "next": {"done": "join"}},
            "join": {"step": "echo", "input": {"from": "sibling"}},
        },
        "results": [{"from": "join"}],
    }


@pytest.mark.parametrize("mode", [StepMode.DATABASE, StepMode.IO])
def test_active_settlement_data_error_closes_attempt_and_can_be_retried(execution, register_step, monkeypatch, mode):
    """A rejected result leaves durable failure evidence instead of an unrecoverable running row."""
    actor, _ = execution
    settlements = []
    original = StepRunQuerySet.settle

    class ResultStep(Echo):
        pass

    ResultStep.mode = mode
    register_step(ResultStep)
    workflow = load_workflow(document("entry"), key="active_result_error", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, input={"value": 13})
    step = retained_step(run)

    def reject_once(self, row, settlement, **kwargs):
        settlements.append(type(settlement.settlement).__name__.lower())
        if settlements == ["done"]:
            raise DataError("Result cannot be stored")
        return original(self, row, settlement, **kwargs)

    monkeypatch.setattr(StepRunQuerySet, "settle", reject_once)
    assert runner.execute(step.pk)
    step.refresh_from_db()
    assert settlements == ["done", "fail"]
    assert step.status == StepRunStatus.FAILED
    attempt = system_queryset(StepAttempt).get(step_run=step)
    assert attempt.result == "failed" and attempt.finished_at is not None
    assert attempt.error == "DataError: Result cannot be stored" and "DataError" in attempt.stacktrace
    assert terminal_fields(run)["status"] == RunStatus.FAILED

    StepRun.objects.retry_step(step, actor=actor)
    assert retained_step(run).status == StepRunStatus.READY
    assert runner.execute(step.pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED
    assert retained_step(run).output == {"value": 13}


@pytest.mark.parametrize("mode", [StepMode.DATABASE, StepMode.IO])
def test_output_nul_characters_are_removed_before_persistence(execution, register_step, mode):
    """A JSON-compatible body result cannot poison PostgreSQL jsonb with a NUL character."""
    actor, _ = execution

    class TextOutput(Step[dict[str, str], dict[str, str], None]):
        key = "text_output"

        def run(self, ctx):
            return ctx.done({"text": "before\x00after"})

    TextOutput.mode = mode
    register_step(TextOutput)
    workflow = load_workflow(document("entry", step=TextOutput.key), key="sanitized_output", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(run).pk)
    assert retained_step(run).output == {"text": "beforeafter"}
    assert terminal_fields(run)["output"] == {"text": "beforeafter"}


def test_attempt_diagnostics_are_sanitized_to_their_declared_column_bounds(execution, register_step):
    """Error retention cannot fail because the original exception exceeds its evidence columns."""
    actor, _ = execution
    bounds = {name: StepAttempt._meta.get_field(name).max_length for name in ("error", "stacktrace")}

    class LongFailure(Echo):
        def run(self, ctx):
            raise ValueError("before\x00after" + "x" * max(bounds.values()))

    register_step(LongFailure)
    workflow = load_workflow(document("entry"), key="bounded_diagnostics", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(run).pk)
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    for name, bound in bounds.items():
        value = getattr(attempt, name)
        assert len(value) == bound and "\x00" not in value
    assert attempt.error.startswith("beforeafter")
    assert retained_step(run).output == {"error": attempt.error}


@pytest.mark.parametrize("sqlstate", ["57014", "40P01", "55P03"])
def test_database_settlement_operational_error_rolls_back_claim_for_redelivery(
    execution, register_step, monkeypatch, sqlstate,
):
    """A transient settlement transaction failure rolls back the DATABASE body and claim."""
    actor, _ = execution
    original = StepRunQuerySet.settle

    class DatabaseWrite(Echo):
        def run(self, ctx):
            Workflow.objects.filter(pk=ctx.run.version.workflow_id).update(name="Body transaction")
            return ctx.done(ctx.input)

    register_step(DatabaseWrite)
    workflow = load_workflow(document("entry"), key="settlement_redelivery", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)

    def unavailable(self, row, settlement, **kwargs):
        raise OperationalError("Retry the DATABASE transaction") from psycopg.errors.lookup(sqlstate)("Transient")

    monkeypatch.setattr(StepRunQuerySet, "settle", unavailable)
    with pytest.raises(OperationalError, match="Retry the DATABASE transaction"):
        runner.execute(step.pk)
    assert retained_step(run).status == StepRunStatus.READY
    assert not system_queryset(StepAttempt).filter(step_run=step).exists()
    assert system_queryset(Workflow).get(pk=workflow.pk).name != "Body transaction"
    monkeypatch.setattr(StepRunQuerySet, "settle", original)
    assert runner.execute(step.pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED


@pytest.mark.parametrize("failure", ["retryable", "operational"])
@pytest.mark.parametrize("idempotent", [False, True])
def test_effect_marker_controls_retryable_body_failures(execution, register_step, failure, idempotent):
    """A possible non-idempotent effect requires acknowledgement even for transient failures."""
    actor, _ = execution
    acknowledgements = []

    class MarkedFailure(Echo):
        mode = StepMode.IO
        effect_idempotent = idempotent
        retry = RetryPolicy(max_attempts=2, backoff=timedelta())

        def run(self, ctx):
            acknowledgements.append(ctx.retry_acknowledged)
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                if failure == "retryable":
                    raise Retryable("Temporary upstream failure")
                raise OperationalError("Temporary database failure") from psycopg.errors.DeadlockDetected("Transient")
            return ctx.done(ctx.input)

    register_step(MarkedFailure)
    workflow = load_workflow(document("entry"), key="marked_retryable", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk)
    step.refresh_from_db()
    assert step.status == StepRunStatus.WAITING
    assert step.waiting_kind == ("time" if idempotent else "error")
    if idempotent:
        assert runner.wake() == 1
    else:
        assert step.wait_reason.lower() == "possible duplicate effect"
        assert runner.wake() == 0
        with pytest.raises(ValidationError, match="duplicate"):
            StepRun.objects.retry_step(step, actor=actor)
        StepRun.objects.retry_step(step, actor=actor, accept_duplicate=True)
    assert runner.execute(step.pk)
    assert acknowledgements == [False, not idempotent]
    attempts = list(system_queryset(StepAttempt).filter(step_run=step).order_by("number"))
    assert attempts[0].effect_started_at is not None
    assert attempts[1].acknowledged_by_id == (None if idempotent else actor.pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED


def test_retry_checks_earlier_effect_markers_after_an_unmarked_failed_attempt(execution, register_step):
    """An unmarked retry cannot erase the duplicate risk retained by an earlier attempt."""
    actor, _ = execution

    class HistoricalMarker(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                return ctx.fail("Effect result uncertain")
            if ctx.attempt.number == 2:
                return ctx.fail("Retry failed before its effect")
            return ctx.done(ctx.input)

    register_step(HistoricalMarker)
    workflow = load_workflow(document("entry"), key="historical_marker", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk)
    StepRun.objects.retry_step(step, actor=actor, accept_duplicate=True)
    assert runner.execute(step.pk)
    assert system_queryset(StepAttempt).get(step_run=step, number=2).effect_started_at is None
    with pytest.raises(ValidationError, match="duplicate"):
        StepRun.objects.retry_step(step, actor=actor)
    StepRun.objects.retry_step(step, actor=actor, accept_duplicate=True)
    assert runner.execute(step.pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED


@pytest.mark.parametrize("continuation", ["wait", "next_page"])
def test_effect_marker_history_survives_wait_but_ends_at_page_completion(execution, register_step, continuation):
    """Only a completed page clears the duplicate risk retained across attempts."""
    actor, _ = execution

    class ContinuedEffect(Echo):
        mode = StepMode.IO
        retry = RetryPolicy(max_attempts=5, backoff=timedelta())

        def run(self, ctx):
            if ctx.attempt.number == 1:
                ctx.begin_effect()
                return ctx.fail("Effect result uncertain")
            if ctx.attempt.number == 2:
                if continuation == "wait":
                    return ctx.wait(until=ctx.now - timedelta(seconds=1))
                return ctx.next_page()
            if ctx.attempt.number == 3:
                raise Retryable("Try this page again")
            return ctx.done(ctx.input)

    register_step(ContinuedEffect)
    workflow = load_workflow(document("entry"), key="page_marker_scope", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk)
    StepRun.objects.retry_step(step, actor=actor, accept_duplicate=True)
    assert runner.execute(step.pk)
    if continuation == "wait":
        assert runner.wake() == 1
    assert runner.execute(step.pk)
    step.refresh_from_db()
    assert step.waiting_kind == ("error" if continuation == "wait" else "time")
    if continuation == "wait":
        with pytest.raises(ValidationError, match="duplicate"):
            StepRun.objects.retry_step(step, actor=actor)
        StepRun.objects.retry_step(step, actor=actor, accept_duplicate=True)
    else:
        assert runner.wake() == 1
    assert runner.execute(step.pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED


def test_expired_attempt_rejects_every_context_fence_before_reaping(execution, register_step):
    """A committed deadline supersedes every helper as well as the eventual settlement."""
    actor, _ = execution
    checked = []

    class ExpiredBody(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            with system_context(reason="test elapsed committed IO deadline"):
                StepRun.objects.filter(pk=ctx.step_run.pk).update(deadline_at=Now() - timedelta(seconds=1))
            operations = (ctx.begin_effect, ctx.heartbeat, ctx.raise_if_canceled, lambda: ctx.record(ctx.run))
            for operation in operations:
                with pytest.raises(Superseded):
                    operation()
                checked.append(True)
            return ctx.done({"value": 99})

    register_step(ExpiredBody)
    workflow = load_workflow(document("entry"), key="all_expired_fences", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk) is False
    assert len(checked) == 4
    assert not system_queryset(StepRecord).filter(step_run=step).exists()
    attempt = system_queryset(StepAttempt).get(step_run=step)
    assert attempt.effect_started_at is None and attempt.finished_at is None
    assert runner.reap() == 1
    assert retained_step(run).output == {"error": "The attempt deadline expired."}


def test_retry_names_other_unrouted_failures_without_reopening_the_run(execution, register_step):
    """Simultaneous IO failures require an explicit decision about every failed branch."""
    actor, _ = execution

    class SecondFailure(Echo):
        key = "second_failure"
        mode = StepMode.IO

        def run(self, ctx):
            assert runner.execute(retained_step(ctx.run, "failed").pk)
            return ctx.fail("The sibling also failed")

    register_step(SecondFailure)
    workflow = load_workflow(fork(SecondFailure.key), key="multiple_failures", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(run).pk)
    assert runner.execute(retained_step(run, "sibling").pk)
    before = terminal_fields(run)
    for target, other in (("failed", "sibling"), ("sibling", "failed")):
        with pytest.raises(ValidationError, match=other):
            StepRun.objects.retry_step(retained_step(run, target), actor=actor)
        assert terminal_fields(run) == before
        assert retained_step(run, target).status == StepRunStatus.FAILED


def test_retry_of_a_deleted_run_has_a_defined_validation_error(execution):
    """A retained operator selection cannot turn deletion into an internal exception."""
    actor, _ = execution
    workflow = load_workflow(document("entry", step="reject"), key="deleted_retry", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk)
    with system_context(reason="test removed retry target"):
        WorkflowRun.objects.filter(pk=run.pk).delete()
    with pytest.raises(ValidationError):
        StepRun.objects.retry_step(step, actor=actor)


@pytest.mark.parametrize("sibling_status", ["ready", "waiting", "running"])
def test_cancel_abandoned_failed_run_closes_open_siblings_without_changing_terminal_fields(
    execution, register_step, sibling_status,
):
    """Terminal cleanup preserves the failure and supersedes any still-running IO attempt."""
    actor, _ = execution
    snapshot = {}

    class OpenSibling(Echo):
        key = "open_sibling"
        mode = StepMode.IO

        def run(self, ctx):
            if sibling_status == "waiting":
                return ctx.wait(until=ctx.now + timedelta(hours=1))
            assert runner.execute(retained_step(ctx.run, "failed").pk)
            snapshot.update(terminal_fields(ctx.run))
            WorkflowRun.objects.cancel(ctx.run, actor=actor)
            with pytest.raises(Superseded):
                ctx.begin_effect()
            return ctx.done(ctx.input)

    register_step(OpenSibling)
    workflow = load_workflow(fork(OpenSibling.key), key="abandoned_failed_run", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(run).pk)
    sibling = retained_step(run, "sibling")
    if sibling_status == "running":
        assert runner.execute(sibling.pk) is False
    else:
        if sibling_status == "waiting":
            assert runner.execute(sibling.pk)
        assert runner.execute(retained_step(run, "failed").pk)
        snapshot.update(terminal_fields(run))
        assert retained_step(run, "sibling").status == sibling_status
        WorkflowRun.objects.cancel(run, actor=actor)
    assert snapshot["status"] == RunStatus.FAILED
    assert terminal_fields(run) == snapshot
    assert retained_step(run, "sibling").status == StepRunStatus.CANCELED
    assert retained_step(run, "failed").status == StepRunStatus.FAILED
    assert not system_queryset(StepAttempt).filter(step_run__run=run, finished_at__isnull=True).exists()
    if sibling_status == "running":
        assert system_queryset(StepAttempt).get(step_run=sibling).result == "superseded"


@pytest.mark.parametrize("result", ["failed", "superseded", "succeeded"])
def test_artifacts_commit_only_with_successful_attempt_evidence(execution, register_step, monkeypatch, result):
    """IO-created evidence is staged until its own successful settlement commits."""
    actor, _ = execution
    publications, after_cancel = [], []
    original_publish = managers.publish_change

    def publish(run, **kwargs):
        publications.append(run.pk)
        return original_publish(run, **kwargs)

    monkeypatch.setattr(managers, "publish_change", publish)
    monkeypatch.setattr(runner_module, "publish_change", publish)

    class Evidence(Echo):
        mode = StepMode.IO

        def run(self, ctx):
            evidence = ctx.record(ctx.run, label="Result evidence")
            assert evidence.pk is None
            assert not system_queryset(StepRecord).filter(step_run=ctx.step_run).exists()
            if result == "failed":
                return ctx.fail("The work failed")
            if result == "superseded":
                WorkflowRun.objects.cancel(ctx.run, actor=actor)
                after_cancel.append(len(publications))
            return ctx.done(ctx.input)

    register_step(Evidence)
    workflow = load_workflow(document("entry"), key="settled_artifacts", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    step = retained_step(run)
    assert runner.execute(step.pk) is (result != "superseded")
    assert system_queryset(StepRecord).filter(step_run=step).count() == (1 if result == "succeeded" else 0)
    if result == "superseded":
        assert len(publications) == after_cancel[0]


def test_reprocess_origin_is_present_at_its_first_change_publication(execution, monkeypatch):
    """Observers never receive a replacement run whose admission still looks manual."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="published_reprocess_origin", actor=actor)
    predecessor = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(predecessor).pk)
    observations = []
    original = runner_module.publish_change

    def observe(run, **kwargs):
        observations.append((
            run.pk, run.origin, run.reprocess_of_id,
            system_queryset(WorkflowRun).get(pk=run.pk).reprocess_of_id,
        ))
        return original(run, **kwargs)

    monkeypatch.setattr(runner_module, "publish_change", observe)
    replacement = WorkflowRun.objects.reprocess(predecessor, actor=actor)
    assert observations == [(replacement.pk, "reprocess", predecessor.pk, predecessor.pk)]


def test_cancel_authorizes_once_before_acquiring_the_run_lock(execution, monkeypatch):
    """Cancellation cannot hold engine locks while it resolves requester authorization."""
    actor, _ = execution
    workflow = load_workflow(document("entry"), key="cancel_authorization_order", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    observed = []
    original_access, original_hold = WorkflowRun.require_access, WorkflowRunQuerySet.hold

    def require_access(self, permission, actor=None):
        assert not connection.in_atomic_block
        observed.append(("authorize", permission))
        return original_access(self, permission, actor)

    @contextmanager
    def hold(self, run_id, **kwargs):
        observed.append(("lock", run_id))
        with original_hold(self, run_id, **kwargs) as locked:
            yield locked

    monkeypatch.setattr(WorkflowRun, "require_access", require_access)
    monkeypatch.setattr(WorkflowRunQuerySet, "hold", hold)
    WorkflowRun.objects.cancel(run, actor=actor)
    assert observed == [("authorize", "write"), ("lock", run.pk)]
    assert terminal_fields(run)["status"] == RunStatus.CANCELED


def test_heartbeat_preserves_the_absolute_worker_settlement_window(execution, register_step, settings):
    """Repeated lease extensions cannot consume the reserved time before the worker limit."""
    actor, _ = execution
    settings.CELERY_TASK_SOFT_TIME_LIMIT = 120
    settings.CELERY_TASK_TIME_LIMIT = 180

    class BoundedHeartbeat(Echo):
        mode = StepMode.IO
        timeout = timedelta(seconds=30)

        def run(self, ctx):
            with system_context(reason="test elapsed worker execution time"):
                StepAttempt.objects.filter(pk=ctx.attempt.pk).update(started_at=Now() - timedelta(seconds=70))
            started_at = system_queryset(StepAttempt).get(pk=ctx.attempt.pk).started_at
            ctx.heartbeat()
            assert ctx.now < ctx.step_run.deadline_at <= started_at + timedelta(seconds=90)
            return ctx.done(ctx.input)

    register_step(BoundedHeartbeat)
    workflow = load_workflow(document("entry"), key="worker_heartbeat_bound", actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(retained_step(run).pk)
    assert terminal_fields(run)["status"] == RunStatus.SUCCEEDED
