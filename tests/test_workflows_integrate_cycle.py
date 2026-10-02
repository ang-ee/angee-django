"""Bridge scheduling, actor admission and terminal workflow settlement contracts."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.models.functions import Now
from django.utils import timezone
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.integrate import scheduler
from angee.integrate import sync_runner as integrate_sync_runner
from angee.integrate.models import Bridge, IntegrationLifecycle, IntegrationRuntimeStatus
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.sync import SyncDispatch
from angee.integrate.sync_runner import run_bridge_sync_job
from angee.workflows import tasks
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.steps import StepMode
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepAttempt, StepRun, WorkflowRun
from angee.workflows_integrate.steps import StreamStage, StreamStageOutput
from angee.workflows_integrate.testing.models import SyncCycleTestBridge
from tests.conftest import make_integration
from tests.test_workflows_review_concurrency import submit, wait_for_lock
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def cycle_factory(execution):
    """Install as an administrator and connect a distinct regular owner's bridge."""
    admin, _sent = execution

    def create(declaration=None, *, input=None, connect=True):
        workflow = load_workflow(
            declaration or document("entry"), key=SyncCycleTestBridge.sync_workflow_key,
            actor=admin, subject_model=SyncCycleTestBridge._meta.label,
        )
        bridge = make_integration(
            "sync-cycle", model=SyncCycleTestBridge, config={"input": input or {}},
            lifecycle=IntegrationLifecycle.DISCONNECTED, next_sync_at=timezone.now(), poll_interval=37,
        )
        if connect:
            with system_context(reason="test connect workflow bridge"):
                bridge.connect()
        return workflow, bridge

    return create


def dispatch(bridge):
    """Queue and deliver through integrate's public worker entrypoint."""
    now = timezone.now()
    queue_bridge_sync(bridge, now=now)
    assert run_bridge_sync_job(
        bridge._meta.label_lower, bridge.pk, now, require_queue_token=True,
    ) == {"ok": True, "items": 0, "skipped": False, "dispatched": True}
    bridge.refresh_from_db()
    return system_queryset(WorkflowRun).get(pk=bridge.sync_run_id)


def test_scheduler_dispatches_once_and_request_replay_keeps_the_original_input(
    cycle_factory, execution, monkeypatch,
):
    _workflow, bridge = cycle_factory(input={"value": 7})
    _admin, sent = execution
    now = timezone.now()
    monkeypatch.setattr(scheduler, "models_with", lambda *, base: (SyncCycleTestBridge,))
    snapshots = []
    snapshot = SyncCycleTestBridge.sync_workflow_input

    def observe_snapshot(self):
        snapshots.append(self.pk)
        return snapshot(self)

    monkeypatch.setattr(SyncCycleTestBridge, "sync_workflow_input", observe_snapshot)
    assert scheduler.enqueue_due_bridges(now=now) == {"enqueued": 1, "skipped": 0}
    deliveries = [envelope["kwargs"] for name, envelope in sent if name == "integrate.sync_bridge_now"]
    assert len(deliveries) == 1
    assert run_bridge_sync_job(**deliveries[0], require_queue_token=True)["dispatched"] is True
    assert run_bridge_sync_job(**deliveries[0], require_queue_token=True)["stale"] is True
    assert scheduler.enqueue_due_bridges(now=now + timedelta(minutes=10)) == {"enqueued": 0, "skipped": 0}

    bridge.refresh_from_db()
    run = system_queryset(WorkflowRun).get(pk=bridge.sync_run_id)
    content_type = ContentType.objects.get_for_model(bridge, for_concrete_model=False)
    assert run.request_key == f"bridge-sync:{content_type.pk}:{bridge.pk}:{now.isoformat()}"
    assert run.run_as_id == bridge.owner_id
    assert run.input == {"value": 7}
    with system_context(reason="test input changes after admission"):
        bridge.config = {"input": {"value": 99}}
        bridge.save(update_fields=["config"])
    assert bridge.sync() is SyncDispatch.DISPATCHED
    assert snapshots == [bridge.pk]
    assert system_queryset(WorkflowRun).for_subject(bridge).count() == 1
    run.refresh_from_db()
    assert run.input == {"value": 7}
    assert bridge.sync_is_dispatched and bridge.next_sync_at is None


@pytest.mark.parametrize("terminal", [False, True])
def test_delayed_delivery_reads_the_current_cycle_under_its_row_lock(cycle_factory, monkeypatch, terminal):
    """A stale delivery preserves both an active dispatch and a completed cycle."""
    _workflow, bridge = cycle_factory()
    now = timezone.now()
    queue_bridge_sync(bridge, now=now)
    bridge_model = integrate_sync_runner._bridge_model
    observed = {}
    fields = (
        "sync_run_id", "sync_stage", "last_sync_started_at", "last_sync_completed_at", "sync_progress", "next_sync_at",
    )

    def first_delivery_wins(model_label):
        with monkeypatch.context() as first_delivery:
            first_delivery.setattr(integrate_sync_runner, "_bridge_model", bridge_model)
            assert run_bridge_sync_job(
                bridge._meta.label_lower, bridge.pk, now, require_queue_token=True,
            )["dispatched"] is True
            bridge.refresh_from_db()
            run = system_queryset(WorkflowRun).get(pk=bridge.sync_run_id)
            if terminal:
                run_until(run)
            observed.update(system_queryset(SyncCycleTestBridge).filter(pk=bridge.pk).values(*fields).get())
        return bridge_model(model_label)

    monkeypatch.setattr(integrate_sync_runner, "_bridge_model", first_delivery_wins)

    result = run_bridge_sync_job(
        bridge._meta.label_lower, bridge.pk, now, require_queue_token=terminal,
    )

    assert result == (
        {"ok": True, "items": 0, "skipped": True, "stale": True} if terminal else
        {"ok": True, "items": 0, "skipped": False, "dispatched": True}
    )
    assert system_queryset(SyncCycleTestBridge).filter(pk=bridge.pk).values(*fields).get() == observed
    assert system_queryset(WorkflowRun).for_subject(bridge).count() == 1
    bridge.refresh_from_db()
    assert bridge.sync_is_dispatched is not terminal


@pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required.")
def test_duplicate_delivery_cannot_clear_the_winners_queue_token(cycle_factory, monkeypatch):
    """The winner consumes its token under the row lock before a loser can clear it."""
    _workflow, bridge = cycle_factory()
    now = timezone.now()
    queue_bridge_sync(bridge, now=now)
    acquired, release_winner = Event(), Event()
    advisory_lock = integrate_sync_runner.bridge_advisory_lock

    @contextmanager
    def pause_acquired(row):
        with advisory_lock(row) as held:
            if held:
                acquired.set()
                assert release_winner.wait(10)
            yield held

    monkeypatch.setattr(integrate_sync_runner, "bridge_advisory_lock", pause_acquired)

    def deliver():
        return run_bridge_sync_job(bridge._meta.label_lower, bridge.pk, now, require_queue_token=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        winner, _ = submit(pool, deliver)
        try:
            assert acquired.wait(10)
            duplicate, pid = submit(pool, deliver)
            wait_for_lock(pid, duplicate)
        finally:
            release_winner.set()
        assert winner.result(timeout=10) == {"ok": True, "items": 0, "skipped": False, "dispatched": True}
        assert duplicate.result(timeout=10) == {"ok": True, "items": 0, "skipped": True, "stale": True}

    bridge.refresh_from_db()
    assert bridge.sync_is_dispatched
    run = system_queryset(WorkflowRun).for_subject(bridge).get()
    run_until(run)
    bridge.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED and bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.settle_dispatch(run.pk) is False


def test_queued_sync_releases_its_row_transaction_before_the_sync_body(cycle_factory, monkeypatch):
    """The advisory lock spans sync work without carrying the queue row transaction."""
    _workflow, bridge = cycle_factory()
    run_sync = SyncCycleTestBridge.run_sync
    observed = []

    def outside_transaction(self, *, now):
        observed.append(connection.in_atomic_block)
        return run_sync(self, now=now)

    monkeypatch.setattr(SyncCycleTestBridge, "run_sync", outside_transaction)
    dispatch(bridge)
    assert observed == [False]


def test_ui_cancel_settles_once_and_repeated_delivery_cannot_overwrite_it(cycle_factory, execution):
    _workflow, bridge = cycle_factory()
    run = dispatch(bridge)
    _admin, sent = execution
    WorkflowRun.objects.cancel_on_commit(run, bridge.owner)
    cancellations = [envelope["kwargs"] for name, envelope in sent if name == "workflows.cancel"]
    assert len(cancellations) == 1
    tasks.cancel(**cancellations[0])
    run.refresh_from_db()
    bridge.refresh_from_db()
    assert run.status == RunStatus.CANCELED
    assert bridge.sync_stage == Bridge.SyncStage.FAILED
    assert bridge.sync_error == "Sync workflow was canceled."
    assert bridge.runtime_status == IntegrationRuntimeStatus.ERROR
    assert bridge.last_error == bridge.sync_error
    settled = (bridge.last_sync_completed_at, bridge.next_sync_at, bridge.sync_progress, run.finished_at)

    WorkflowRun.objects.cancel_on_commit(run, bridge.owner)
    tasks.cancel(**cancellations[0])
    assert bridge.settle_dispatch(run.pk, result=900) is False
    run.refresh_from_db()
    bridge.refresh_from_db()
    assert (bridge.last_sync_completed_at, bridge.next_sync_at, bridge.sync_progress, run.finished_at) == settled
    assert bridge.sync_error == "Sync workflow was canceled."
    assert not bridge.sync_is_dispatched


@pytest.mark.parametrize("timeout", [False, True])
def test_failure_and_worker_timeout_settle_the_bridge_as_failed(cycle_factory, register_step, timeout):
    class FailingCycle(Echo):
        key = "failing_cycle"
        mode = StepMode.IO

        def run(self, ctx):
            if timeout:
                raise SoftTimeLimitExceeded()
            return ctx.fail("The fixture could not sync.")

    register_step(FailingCycle)
    _workflow, bridge = cycle_factory(document("entry", step=FailingCycle.key))
    run = dispatch(bridge)
    before = timezone.now()
    run_until(run)
    after = timezone.now()
    bridge.refresh_from_db()
    assert run.status == RunStatus.FAILED
    assert bridge.sync_stage == Bridge.SyncStage.FAILED
    assert bridge.sync_error == "Sync workflow failed."
    assert bridge.runtime_status == IntegrationRuntimeStatus.ERROR
    assert before + timedelta(seconds=37) <= bridge.next_sync_at <= after + timedelta(seconds=37)
    assert bridge.settle_dispatch(run.pk) is False
    attempt = system_queryset(StepAttempt).get(step_run__run=run)
    assert attempt.result == ("timed_out" if timeout else "failed")


def test_success_sums_stream_outputs_including_mapped_partitions(cycle_factory, register_step):
    class CountedStream(StreamStage):
        """Emit typed stream counts without introducing an external adapter here."""

        key = "counted_stream"

        def run(self, ctx):
            return ctx.done(StreamStageOutput(
                counts={"cycle_items": int(ctx.input.partition)}, discrepancy_ids=[], evidence=[],
            ))

    register_step(CountedStream)
    _workflow, bridge = cycle_factory({
        "nodes": {
            "first": {
                "step": CountedStream.key,
                "input": {"key": {"value": "records"}, "partition": {"value": "2"}},
                "next": {"done": "partitions"},
            },
            "partitions": {
                "step": "map",
                "input": {"items": {"value": [
                    {"key": "records", "partition": "3"}, {"key": "records", "partition": "5"},
                ]}},
                "body": {"step": CountedStream.key},
            },
        },
        "results": [{"from": "partitions"}],
    })
    run = dispatch(bridge)
    run_until(run)
    bridge.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    assert system_queryset(StepRun).filter(run=run, node_key="partitions.body").count() == 2
    assert bridge.last_sync_items == 10
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_error == "" and bridge.runtime_status == IntegrationRuntimeStatus.OK
    assert bridge.next_sync_at == bridge.last_sync_completed_at + timedelta(seconds=37)
    assert bridge.settle_dispatch(run.pk, result=99) is False


def test_operator_retry_reclaims_the_same_cycle_and_settles_again(cycle_factory, register_step):
    class FailOnce(Echo):
        key = "cycle_fail_once"

        def run(self, ctx):
            if ctx.attempt.number == 1:
                return ctx.fail("Retry this cycle.")
            return ctx.done(ctx.input)

    register_step(FailOnce)
    _workflow, bridge = cycle_factory(document("entry", step=FailOnce.key))
    run = dispatch(bridge)
    run_until(run)
    bridge.refresh_from_db()
    assert run.status == RunStatus.FAILED and not bridge.sync_is_dispatched
    failed = system_queryset(StepRun).get(run=run)

    ready = StepRun.objects.retry_step(failed, actor=bridge.owner)

    bridge.refresh_from_db()
    run.refresh_from_db()
    assert ready.status == StepRunStatus.READY and run.status == RunStatus.RUNNING
    assert bridge.sync_run_id == run.pk and bridge.sync_is_dispatched
    assert bridge.sync_error == "" and bridge.next_sync_at is None
    run_until(run)
    bridge.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.runtime_status == IntegrationRuntimeStatus.OK
    assert system_queryset(WorkflowRun).for_subject(bridge).count() == 1


def test_operator_retry_preserves_an_active_cycle_after_delivery_exhaustion(cycle_factory, settings):
    """Retrying operator wait reuses the busy claim without resetting its telemetry."""
    settings.ANGEE_WORKFLOW_MAX_DISPATCHES = 2
    _workflow, bridge = cycle_factory()
    run = dispatch(bridge)
    started_at = bridge.last_sync_started_at
    progress = bridge.sync_progress
    row = system_queryset(StepRun).get(run=run)
    for _ in range(2):
        with system_context(reason="test bridge cycle delivery exhaustion"):
            StepRun.objects.filter(pk=row.pk).update(dispatched_at=Now() - timedelta(seconds=61))
        assert runner.redispatch() == 1
    row.refresh_from_db()
    run.refresh_from_db()
    bridge.refresh_from_db()
    assert row.waiting_kind == "operator" and run.status == RunStatus.WAITING
    assert bridge.sync_is_dispatched and bridge.sync_run_id == run.pk

    ready = StepRun.objects.retry_step(row, actor=bridge.owner)

    bridge.refresh_from_db()
    run.refresh_from_db()
    assert ready.dispatches == 0 and ready.status == StepRunStatus.READY
    assert run.status == RunStatus.RUNNING
    assert bridge.sync_is_dispatched and bridge.sync_run_id == run.pk
    assert bridge.last_sync_started_at == started_at and bridge.sync_progress == progress
    run_until(run)
    bridge.refresh_from_db()
    assert run.status == RunStatus.SUCCEEDED and bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert system_queryset(WorkflowRun).for_subject(bridge).count() == 1
    assert system_queryset(StepAttempt).filter(step_run__run=run).count() == 1
    assert bridge.settle_dispatch(run.pk, result=99) is False


def test_admission_refuses_a_second_active_cycle(cycle_factory):
    workflow, bridge = cycle_factory()
    run = dispatch(bridge)

    with pytest.raises(ValidationError, match="already has an active cycle"):
        WorkflowRun.objects.start(workflow, actor=bridge.owner, subject=bridge, request_key="another-cycle")

    bridge.refresh_from_db()
    assert bridge.sync_run_id == run.pk and bridge.sync_is_dispatched
    assert system_queryset(WorkflowRun).for_subject(bridge).count() == 1


def test_connect_grants_the_regular_owner_start_access_before_dispatch(cycle_factory):
    workflow, bridge = cycle_factory(connect=False)
    queue_bridge_sync(bridge)
    with pytest.raises(PermissionDenied):
        workflow.require_access("start", bridge.owner)
    with pytest.raises(PermissionDenied):
        bridge.sync()
    assert not system_queryset(WorkflowRun).for_subject(bridge).exists()

    with system_context(reason="test connect grants sync workflow starter"):
        bridge.connect()

    workflow.require_access("start", bridge.owner)
    assert bridge.sync() is SyncDispatch.DISPATCHED
    run = system_queryset(WorkflowRun).for_subject(bridge).get()
    assert run.run_as_id == bridge.owner_id
    assert bridge.lifecycle == IntegrationLifecycle.CONNECTED
