"""Tests for integrate registry discovery and bridge scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.db import close_old_connections, connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)
from rebac import system_context

from angee.integrate import scheduler as integrate_scheduler
from angee.integrate import sync_runner as integrate_sync_runner
from angee.integrate import tasks as integrate_tasks
from angee.integrate.errors import IntegrationError
from angee.integrate.models import Bridge, IntegrationLifecycle, IntegrationRuntimeStatus
from angee.integrate.registry import models_with
from angee.integrate.scheduler import enqueue_due_bridges
from angee.integrate.states import StreamDefinition
from angee.integrate.sync import current_progress_bridge
from tests.conftest import (
    Integration,
    make_integration,
)


class SchedulerBridge(Bridge, Integration):
    """Concrete bridge fixture driven by the integrate scheduler tests."""

    class Meta(Bridge.Meta):
        """Django model options for the scheduler bridge fixture."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_scheduler_bridge"
        rebac_resource_type = "tests/scheduler_bridge"

    def sync(self) -> int:
        """Pretend to synchronize vendor rows and persist canonical stream progress."""

        if self.config.get("mode") == "error":
            raise RuntimeError("vendor unavailable")
        if self.config.get("mode") == "refused":
            raise IntegrationError("Vendor refused the login for ada@example.com.")
        items = int(self.config.get("items", 1))
        if self.config.get("assert_locked"):
            assert self.is_syncing is True
        if self.config.get("progress"):
            progress = current_progress_bridge()
            assert progress is not None
            progress.report_progress(
                "discovering",
                message="Scanning vendor rows",
                details={"items": items, "source": "fixture"},
            )
        if self.config.get("detached_progress"):
            detached = type(self).objects.sudo(reason="test detached bridge progress").get(pk=self.pk)
            detached.report_progress(
                "discovering",
                message="Reached the sync budget",
                details={"items": items, "budget_exhausted": True, "cursor": {"seen": items}},
            )
        manager = self.sync_streams.model.objects
        stream = manager.current(self, StreamDefinition(key="records"))
        manager.advance(stream, {"seen": items}, exhausted=True)
        return items

    def handle_webhook(self, payload: Any) -> None:
        """Accept one webhook payload for the test fixture."""

        del payload

    def verify_webhook(self, request: Any) -> bool:
        """Return whether a webhook request is accepted by the fixture."""

        del request
        return True

    def start_live(self) -> None:
        """Start the fixture live subscription."""

    def stop_live(self) -> None:
        """Stop the fixture live subscription."""


def _enqueue_and_run_due(*, now: datetime) -> dict[str, int]:
    """Drive the production enqueue path with an in-process test worker."""

    ran = 0
    errors = 0
    original = SchedulerBridge.request_sync

    def run_queued(bridge: SchedulerBridge, *, now: datetime | None = None, persist: bool = True) -> bool:
        nonlocal ran, errors
        assert persist is False
        assert now is not None
        try:
            result = integrate_sync_runner.run_bridge_sync_job(
                bridge._meta.label_lower,
                bridge.pk,
                now.isoformat(),
                require_queue_token=True,
            )
        except Exception:
            ran += 1
            errors += 1
        else:
            if not result.get("skipped"):
                ran += 1
        return True

    SchedulerBridge.request_sync = run_queued
    try:
        enqueue_due_bridges(now=now)
    finally:
        SchedulerBridge.request_sync = original
    return {"ran": ran, "errors": errors}


@pytest.fixture(autouse=True)
def _scan_only_the_fixture_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope the scheduler's model scan to this module's fixture bridge.

    The shared test registry accumulates concrete bridge models from other
    modules (posts' Feed, messaging's Channel) whose managed tables may not
    exist in this session, so an unscoped scan fails on table/relation state
    these tests don't own. Cross-model discovery itself is covered by
    ``test_integrate_registry_discovers_bridges_in_deterministic_order``,
    which reads the registry without querying rows.
    """

    monkeypatch.setattr(integrate_scheduler, "models_with", lambda *, base: (SchedulerBridge,))


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridges_run_only_due_rows(transactional_db: None) -> None:
    """The scheduler runs due rows and skips future or unscheduled rows."""

    del transactional_db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        due = make_integration(
            "due-only",
            model=SchedulerBridge,
            config={"items": 2},
            next_sync_at=now - timedelta(seconds=1),
        )
        future = make_integration(
            "future-only",
            model=SchedulerBridge,
            config={"items": 3},
            next_sync_at=now + timedelta(seconds=1),
        )
        unscheduled = make_integration(
            "unscheduled-only",
            model=SchedulerBridge,
            config={"items": 4},
            next_sync_at=None,
        )

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    due.refresh_from_db()
    future.refresh_from_db()
    unscheduled.refresh_from_db()
    assert due.sync_streams.model.objects.current_for_bridge(due, "records").get().cursor == {"seen": 2}
    assert not future.sync_streams.model.objects.current_for_bridge(future, "records").exists()
    assert not unscheduled.sync_streams.model.objects.current_for_bridge(unscheduled, "records").exists()


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_persists_success_telemetry(transactional_db: None) -> None:
    """Successful syncs persist scheduler telemetry, cursor, count, and next run."""

    del transactional_db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "success-telemetry",
            model=SchedulerBridge,
            config={"items": 7},
            poll_interval=42,
            next_sync_at=now,
        )
        integration = Integration.objects.get(pk=bridge.pk)

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_completed_at is not None
    assert bridge.last_sync_completed_at >= now
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.last_sync_items == 7
    assert bridge.sync_error == ""
    assert bridge.sync_progress["items"] == 7
    assert bridge.last_sync_summary["items"] == 7
    assert bridge.sync_streams.model.objects.current_for_bridge(bridge, "records").get().cursor == {"seen": 7}
    assert bridge.next_sync_at == bridge.last_sync_completed_at + timedelta(seconds=42)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"


def test_paused_manual_sync_stays_one_shot_and_out_of_periodic_due_scan(
    db: None,
) -> None:
    """A paused bridge may sync explicitly without rearming periodic polling."""

    del db
    now = timezone.now()
    with system_context(reason="test paused one-shot bridge setup"):
        bridge = make_integration(
            "paused-one-shot",
            model=SchedulerBridge,
            lifecycle=IntegrationLifecycle.PAUSED,
            config={"items": 3},
            next_sync_at=now,
        )

    result = integrate_sync_runner.run_bridge_sync_job(
        bridge._meta.label_lower,
        bridge.pk,
        now.isoformat(),
    )

    assert result == {"ok": True, "items": 3, "skipped": False}
    bridge.refresh_from_db()
    assert bridge.sync_streams.model.objects.current_for_bridge(bridge, "records").get().cursor == {"seen": 3}
    assert bridge.next_sync_at is None
    with system_context(reason="test paused legacy due marker"):
        bridge.next_sync_at = now
        bridge.save(update_fields=["next_sync_at", "updated_at"])
        due = SchedulerBridge.objects.due_for_enqueue(
            timestamp=now,
            stale_before=now - timedelta(minutes=5),
        )
        assert not due.filter(pk=bridge.pk).exists()


@pytest.mark.django_db(transaction=True)
def test_integration_error_reaches_sync_telemetry_verbatim(transactional_db: None) -> None:
    """An IntegrationError's operator-safe message is persisted; other failures stay generic."""

    del transactional_db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "refused-rollup",
            model=SchedulerBridge,
            config={"mode": "refused"},
            next_sync_at=now,
        )
        integration = Integration.objects.get(pk=bridge.pk)

    assert _enqueue_and_run_due(now=now) == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.sync_error == "Vendor refused the login for ada@example.com."
    assert bridge.sync_progress["error"] == "Vendor refused the login for ada@example.com."
    assert integration.last_error == "Vendor refused the login for ada@example.com."
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_records_errors_on_integration_runtime_status(transactional_db: None) -> None:
    """Failing syncs record bridge errors, reschedule, and push integration runtime status."""

    del transactional_db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "error-rollup",
            model=SchedulerBridge,
            config={"mode": "error"},
            poll_interval=17,
            next_sync_at=now,
        )
        integration = Integration.objects.get(pk=bridge.pk)

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.sync_stage == Bridge.SyncStage.FAILED
    assert bridge.sync_error == "Integration operation failed."
    assert bridge.sync_progress["error"] == "Integration operation failed."
    assert bridge.next_sync_at is not None
    assert bridge.next_sync_at >= now + timedelta(seconds=17)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "Integration operation failed."
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_success_recovers_bridge_and_integration_runtime_status(transactional_db: None) -> None:
    """A healthy sync after an error clears the integration runtime status."""

    del transactional_db
    first_now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "recovery",
            model=SchedulerBridge,
            config={"mode": "error"},
            poll_interval=23,
            next_sync_at=first_now,
        )
        integration = Integration.objects.get(pk=bridge.pk)

    error_result = _enqueue_and_run_due(now=first_now)

    assert error_result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR

    second_now = first_now + timedelta(minutes=1)
    with system_context(reason="test integrate scheduler setup"):
        bridge.config = {"items": 5}
        bridge.next_sync_at = second_now
        bridge.save(update_fields=["config", "next_sync_at", "updated_at"])

    success_result = _enqueue_and_run_due(now=second_now)

    assert success_result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.sync_stage == bridge.SyncStage.COMPLETED
    assert bridge.last_sync_items == 5
    assert bridge.last_sync_completed_at is not None
    assert bridge.next_sync_at == bridge.last_sync_completed_at + timedelta(seconds=23)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"
    assert integration.last_error == ""
    assert integration.last_error_at is None


@pytest.mark.django_db(transaction=True)
def test_bridge_sync_persists_its_progress_report(transactional_db: None) -> None:
    """Bridge.sync can publish generic progress without knowing the storage fields."""

    del transactional_db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "progress-payload",
            model=SchedulerBridge,
            config={"items": 3, "progress": True},
            next_sync_at=now,
        )

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["details"] == {"items": 3, "source": "fixture"}
    assert bridge.sync_progress["message"] == "Scanning vendor rows"


def test_declined_sync_run_releases_its_queue_claim(db: None) -> None:
    """A run that cannot take the lock clears its own claim, so recovery stops.

    The stale-queue sweep exists to repair a *lost* enqueue and cannot tell one from
    a declined run, so a claim left behind is re-queued every recovery window
    forever — which is what a live session's permanently-held lock produced.
    """

    del db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("declined-run", model=SchedulerBridge)
        bridge.mark_sync_queued(now=now)

    # Hold the lock the way a live session does, then let a sync run decline.
    with bridge.sync_lock():
        result = integrate_sync_runner.run_bridge_sync_job(bridge._meta.label_lower, bridge.pk, now.isoformat())

    assert result == {"ok": True, "items": 0, "skipped": True}
    with system_context(reason="test integrate scheduler verify"):
        bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.IDLE


class SyncStageMachine(RuleBasedStateMachine):
    """Requests, scheduler scans, task deliveries, workers and dispatched runs against a small oracle.

    The oracle holds what the verbs must leave on the row: the stage, the retained
    run, the queue token, the reported message and details, and the last run's
    outcome. A worker keeps the instance it started with, as the sync runner does,
    while progress arrives through other instances. Each example rolls back; its
    clock runs a minute per step a day in the past, so completion follows every start.
    """

    def __init__(self) -> None:
        super().__init__()
        self.scope = ExitStack()
        self.scope.enter_context(system_context(reason="test sync stage machine"))
        self.scope.enter_context(transaction.atomic())
        self.pk = make_integration("sync-machine", model=SchedulerBridge, poll_interval=60).pk
        self.clock = timezone.now() - timedelta(days=1)
        self.lock: ExitStack | None = None
        self.worker: SchedulerBridge | None = None
        self.started = self.clock
        self.stage, self.run_id, self.token, self.runs = Bridge.SyncStage.IDLE, None, self.clock, 0
        self.error, self.items, self.last_items, self.completed = "", None, 0, False
        self.progress: dict[str, Any] = {}

    def teardown(self) -> None:
        if self.lock is not None:
            self.lock.close()
        transaction.set_rollback(True)
        self.scope.close()

    def tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    def fetch(self) -> SchedulerBridge:
        return SchedulerBridge.objects.get(pk=self.pk)

    def mark(self, stage: str, *, error: str = "", items: int | None = None) -> None:
        """Every lifecycle marker replaces the previous run's outcome."""
        self.stage, self.error, self.items = stage, error, items
        if items is not None:
            self.last_items, self.completed = items, True

    def admit(self, admitted: bool, now: datetime, before: Any) -> None:
        """Only an unowned sync is admitted; a declined admission leaves the progress untouched."""
        owned = self.worker is not None or self.run_id is not None and self.stage in Bridge.LIVE_SYNC_STAGES
        assert admitted is not owned
        if admitted:
            self.mark(Bridge.SyncStage.QUEUED)
            self.token, self.run_id = now, None
        else:
            assert self.fetch().sync_progress == before

    def release(self) -> None:
        assert self.lock is not None
        self.lock.close()
        self.lock = self.worker = None

    @initialize(history=st.lists(
        st.tuples(st.sampled_from(("finish", "dispatch", "abandon")), st.booleans()), min_size=1, max_size=3,
    ))
    def play(self, history: list[tuple[str, bool]]) -> None:
        """Earlier runs, each requested, delivered and ended as drawn, leave their markers behind."""
        for ending, failed in history:
            self.request()
            self.deliver("current")
            if ending == "finish":
                self.finish(items=1, refused=failed)
            elif ending == "dispatch":
                self.dispatch()
                self.settle(items=2, failed=failed)
            else:
                self.abandon()
            self.row_matches_the_oracle()

    @rule()
    def request(self) -> None:
        now, before = self.tick(), self.fetch()
        progress = before.sync_progress
        self.admit(before.request_sync(now=now), now, progress)

    @rule()
    def scan(self) -> None:
        """A fresh or declined scheduled admission claims the next interval; recovering a queue keeps its claim."""
        now = self.tick()
        with transaction.atomic():
            row = SchedulerBridge.objects.lock_if_supported().get(pk=self.pk)
            claim, progress = row.next_sync_at, row.sync_progress
            admitted = row.admit_due_sync(now=now)
        recovering = admitted and self.stage == Bridge.SyncStage.QUEUED
        assert self.fetch().next_sync_at == (claim if recovering else now + timedelta(seconds=60))
        self.admit(admitted, now, progress)

    @rule(kind=st.sampled_from(("current", "superseded", "contended")))
    def deliver(self, kind: str) -> None:
        """A task carries the last queued token or a superseded one. Behind a held lock (a worker's or a
        live session's) it releases its claim only while that is current; otherwise a current token starts."""
        token = self.token - timedelta(seconds=1) if kind == "superseded" else self.token
        current = self.stage == Bridge.SyncStage.QUEUED and self.token == token
        if self.worker is not None or kind == "contended":
            assert self.fetch().release_sync_queue(now=token) is current
            if current:
                self.stage = Bridge.SyncStage.IDLE
        elif current:
            self.lock, self.worker, self.started = ExitStack(), self.fetch(), token
            assert self.lock.enter_context(self.worker.sync_lock())
            assert self.worker.mark_sync_started(now=token)
            self.mark(Bridge.SyncStage.SYNCING)

    @precondition(lambda self: self.stage in (Bridge.SyncStage.QUEUED, *Bridge.LIVE_SYNC_STAGES))
    @rule(
        stage=st.sampled_from(Bridge.LIVE_SYNC_STAGES),
        message=st.sampled_from(("", "Fetching")),
        seen=st.integers(0, 3),
    )
    def report(self, stage: str, message: str, seen: int) -> None:
        """Progress replaces the message and details, and keeps a queued stage until work starts."""
        self.fetch().report_progress(stage, message=message, details={"seen": seen})
        self.stage = self.stage if self.stage == Bridge.SyncStage.QUEUED else stage
        self.progress = {"details": {"seen": seen}} | ({"message": message} if message else {})

    @precondition(lambda self: self.worker is not None)
    @rule(items=st.integers(0, 5), refused=st.booleans())
    def finish(self, items: int, refused: bool) -> None:
        """The worker settles through its start-time instance while its partitions report through others;
        the terminal marker keeps their progress."""
        assert self.worker is not None
        self.worker.config = {"mode": "refused"} if refused else {"items": items, "detached_progress": True}
        if refused:
            with pytest.raises(IntegrationError):
                self.worker.run_sync(now=self.started)
            self.mark(Bridge.SyncStage.FAILED, error="Vendor refused the login for ada@example.com.")
        else:
            assert self.worker.run_sync(now=self.started) == items
            row = self.fetch()  # The queue token names the attempt; completion is when the work finished.
            assert row.last_sync_started_at == self.started and row.last_sync_completed_at > self.clock
            details = {"items": items, "budget_exhausted": True, "cursor": {"seen": items}}
            self.progress = {"message": "Reached the sync budget", "details": details}
            self.mark(Bridge.SyncStage.COMPLETED, items=items)
        self.release()

    @precondition(lambda self: self.worker is not None)
    @rule()
    def dispatch(self) -> None:
        """The worker's sync hands the occurrence to a workflow run, which owns its terminal report."""
        assert self.worker is not None
        self.runs += 1
        assert self.worker.claim_dispatch(self.runs, now=self.tick())
        self.run_id = self.runs
        self.mark(Bridge.SyncStage.SYNCING)
        self.release()

    @precondition(lambda self: self.worker is not None)
    @rule()
    def abandon(self) -> None:
        """A worker dies without reporting; its stale stage stays behind and owns nothing."""
        self.release()

    @precondition(lambda self: self.run_id is not None)
    @rule(items=st.integers(0, 5), failed=st.booleans())
    def settle(self, items: int, failed: bool) -> None:
        """A retained run settles once and stays for inspection."""
        active = self.stage in Bridge.LIVE_SYNC_STAGES
        outcome = {"error": IntegrationError("Sync workflow failed.")} if failed else {"result": items}
        assert self.fetch().settle_dispatch(self.run_id, **outcome) is active
        if active and failed:
            self.mark(Bridge.SyncStage.FAILED, error="Sync workflow failed.")
        elif active:
            self.mark(Bridge.SyncStage.COMPLETED, items=items)

    @rule()
    def pair(self) -> None:
        """A session that pairs clears a failure, back to the last completion when there was one."""
        self.fetch().clear_sync_error()
        if self.stage == Bridge.SyncStage.FAILED:
            self.mark(Bridge.SyncStage.COMPLETED if self.completed else Bridge.SyncStage.IDLE)

    @invariant()
    def row_matches_the_oracle(self) -> None:
        """Failure markers live only on a failed row, outcomes only on their terminal marker, and every
        marker keeps the reported progress and a queued row's token; the stage lives on its column alone."""
        row = self.fetch()
        progress = row.sync_progress
        assert (row.sync_stage, row.sync_run_id, row.sync_error) == (self.stage, self.run_id, self.error)
        assert "stage" not in progress
        assert (progress.get("error"), progress.get("items"), row.last_sync_items) == (
            self.error or None, self.items, self.last_items,
        )
        assert {key: progress[key] for key in ("message", "details") if key in progress} == self.progress
        if self.stage == Bridge.SyncStage.QUEUED:
            assert progress["queued_at"] == self.token.isoformat()


def test_sync_stage_follows_its_oracle_in_any_interleaving(db: None) -> None:
    """Admission, delivery, progress, settlement and pairing keep the sync stage's contract."""

    del db
    examples = settings(max_examples=25, stateful_step_count=20, deadline=None)
    run_state_machine_as_test(SyncStageMachine, settings=examples)


def test_bridge_is_syncing_uses_live_lock_state(db: None) -> None:
    """The live lock is separate from durable stage telemetry."""

    del db
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("live-lock", model=SchedulerBridge)

    assert bridge.is_syncing is False

    with bridge.sync_lock() as acquired:
        assert acquired is True
        assert bridge.is_syncing is True
        with bridge.sync_lock() as second_acquired:
            assert second_acquired is False

    assert bridge.is_syncing is False


@contextmanager
def _cross_process_locks() -> Iterator[None]:
    """Report the lock backend as cross-process for one assertion block.

    The suite's LocalLockBackend is process-local by design; the reconciliation
    under test only engages against a backend whose locks other processes can
    observe (Postgres advisory locks in deployment).
    """

    import angee.integrate.models as integrate_models

    original = integrate_models.task_locks_are_cross_process
    integrate_models.task_locks_are_cross_process = lambda: True
    try:
        yield
    finally:
        integrate_models.task_locks_are_cross_process = original


def test_effective_sync_stage_reconciles_stale_records_against_the_lock(db: None) -> None:
    """A live-ish persisted stage without the live lock reads as FAILED.

    The persisted ``sync_stage`` is a progress report a crashed worker leaves
    stale; the effective projection trusts the advisory lock instead, so the UI
    never shows a phantom "syncing". ``queued`` legitimately holds no lock (the
    task has not started) and passes through, as do the terminal stages. The
    reconciliation only applies when the lock backend is cross-process — a
    process-local backend (this suite's) cannot see a worker's lock, so the
    column is trusted as-is there.
    """

    del db
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("stale-stage", model=SchedulerBridge)

        # The suite's LocalLockBackend is process-local: no reconciliation.
        bridge.sync_stage = bridge.SyncStage.SYNCING
        assert bridge.effective_sync_stage == bridge.SyncStage.SYNCING

    with _cross_process_locks():
        bridge.sync_stage = bridge.SyncStage.SYNCING
        assert bridge.effective_sync_stage == bridge.SyncStage.FAILED
        with bridge.sync_lock() as acquired:
            assert acquired is True
            assert bridge.effective_sync_stage == bridge.SyncStage.SYNCING
        bridge.sync_stage = bridge.SyncStage.DISCOVERING
        assert bridge.effective_sync_stage == bridge.SyncStage.FAILED
        for stage in (
            bridge.SyncStage.IDLE,
            bridge.SyncStage.QUEUED,
            bridge.SyncStage.COMPLETED,
            bridge.SyncStage.FAILED,
        ):
            bridge.sync_stage = stage
            assert bridge.effective_sync_stage == stage


def test_run_bridge_sync_job_holds_the_live_lock(db: None) -> None:
    """The queued task runner owns the live lock while a bridge sync runs."""

    del db
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "runner-lock",
            model=SchedulerBridge,
            config={"items": 6, "assert_locked": True},
        )

    result = integrate_tasks.run_bridge_sync_job(SchedulerBridge._meta.label_lower, bridge.pk, now.isoformat())

    assert result == {"ok": True, "items": 6, "skipped": False}
    bridge.refresh_from_db()
    assert bridge.sync_streams.model.objects.current_for_bridge(bridge, "records").get().cursor == {"seen": 6}
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED


def test_request_sync_marks_queued_and_defers_task(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queueing persists visible state before the worker picks up the task."""

    del db
    now = timezone.now()
    enqueued: list[tuple[str, dict[str, Any]]] = []

    def fake_enqueue_task(task_name: str, *, kwargs: dict[str, Any], **options: Any) -> None:
        assert options == {}
        enqueued.append((task_name, kwargs))

    monkeypatch.setattr("angee.integrate.models.enqueue_task", fake_enqueue_task)
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-bridge", model=SchedulerBridge)

    assert bridge.request_sync(now=now) is True

    assert enqueued == [
        (
            "integrate.sync_bridge_now",
            {
                "model_label": SchedulerBridge._meta.label_lower,
                "pk": bridge.pk,
                "timestamp": now.isoformat(),
            },
        )
    ]
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_error == ""
    assert bridge.sync_progress == {"queued_at": now.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_declined_scheduled_admission_waits_for_the_next_interval(
    transactional_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A poll declined by a live sync lock leaves the due set until its next interval."""

    del transactional_db
    now = timezone.now()
    later = now + timedelta(seconds=120)
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr("angee.integrate.models.enqueue_task", lambda _name, **kwargs: sent.append(kwargs))
    with system_context(reason="test declined admission setup"):
        bridge = make_integration(
            "declined-due",
            model=SchedulerBridge,
            poll_interval=120,
            next_sync_at=now - timedelta(seconds=1),
        )

    with bridge.sync_lock() as acquired:
        assert acquired
        assert enqueue_due_bridges(now=now) == {"enqueued": 0, "skipped": 1}
        bridge.refresh_from_db()
        assert bridge.next_sync_at == later
        assert bridge.sync_stage == Bridge.SyncStage.IDLE
        # The declined bridge is no longer selected, so its lock is not probed again.
        assert enqueue_due_bridges(now=now + timedelta(seconds=60)) == {"enqueued": 0, "skipped": 0}

    assert sent == []
    assert enqueue_due_bridges(now=later) == {"enqueued": 1, "skipped": 0}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.next_sync_at == later + timedelta(seconds=120)
    assert len(sent) == 1


def test_request_sync_can_enqueue_duplicate_requests(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task-level locks, not the broker, own duplicate execution exclusion."""

    del db
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.integrate.models.enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-duplicate", model=SchedulerBridge)

    bridge.request_sync(now=now)
    bridge.request_sync(now=now)

    assert len(enqueued) == 2
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"queued_at": now.isoformat()}


def test_stale_bridge_sync_task_payload_is_skipped(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate task that no longer matches queued state cannot sync again later."""

    del db
    first = timezone.now()
    second = first + timedelta(seconds=5)
    monkeypatch.setattr(
        "angee.integrate.models.enqueue_task",
        lambda _task_name, *, kwargs, **_options: None,
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-stale", model=SchedulerBridge, config={"items": 9})

    bridge.request_sync(now=first)
    bridge.request_sync(now=second)

    stale = integrate_tasks.sync_bridge_now(SchedulerBridge._meta.label_lower, bridge.pk, first.isoformat())

    assert stale == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert not bridge.sync_streams.model.objects.current_for_bridge(bridge, "records").exists()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"queued_at": second.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_request_sync_inside_outer_transaction_preserves_caller(
    transactional_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queueing preserves its outer transaction and sends only after commit."""

    del transactional_db
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "angee.jobs.enqueue.celery_app.send_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-db-duplicate", model=SchedulerBridge)

    with transaction.atomic():
        bridge.request_sync(now=now)
        assert SchedulerBridge._base_manager.filter(pk=bridge.pk).exists()
        assert enqueued == []

    assert len(enqueued) == 1
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"queued_at": now.isoformat()}


def test_integrate_registry_discovers_bridges_in_deterministic_order(db: None) -> None:
    """Registry helpers include the concrete fixture and sort by model label."""

    del db

    discovered_models = models_with(base=Bridge)
    bridge_labels = tuple(model._meta.label_lower for model in discovered_models)

    assert SchedulerBridge in discovered_models
    assert bridge_labels == tuple(sorted(bridge_labels))


def test_periodic_task_drives_the_due_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The queue tick calls the pure due-scan; registration is by stable task name."""

    calls: list[bool] = []
    monkeypatch.setattr(integrate_tasks.scheduler, "enqueue_pending_discoveries", lambda: calls.append(False))
    monkeypatch.setattr(integrate_tasks.scheduler, "enqueue_due_bridges", lambda: calls.append(True))
    integrate_tasks.sync_due_bridges()

    assert calls == [False, True]
    assert integrate_tasks.sync_due_bridges.name == "integrate.sync_due_bridges"


@pytest.mark.django_db(transaction=True)
def test_enqueue_due_bridges_claims_and_queues_rows(
    transactional_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The periodic scan claims due rows and enqueues bridge work."""

    del transactional_db
    now = timezone.now()
    enqueued: list[tuple[int, datetime | None]] = []

    def fake_request_sync(
        bridge: SchedulerBridge,
        *,
        now: datetime | None = None,
        persist: bool = True,
    ) -> bool:
        assert persist is False
        enqueued.append((bridge.pk, now))
        return True

    monkeypatch.setattr(SchedulerBridge, "request_sync", fake_request_sync)
    with system_context(reason="test integrate scheduler setup"):
        due = make_integration(
            "queued-due",
            model=SchedulerBridge,
            poll_interval=120,
            next_sync_at=now - timedelta(seconds=1),
        )
        future = make_integration(
            "queued-future",
            model=SchedulerBridge,
            next_sync_at=now + timedelta(seconds=1),
        )

    counters = enqueue_due_bridges(now=now)

    assert counters == {"enqueued": 1, "skipped": 0}
    assert enqueued == [(due.pk, now)]
    due.refresh_from_db()
    future.refresh_from_db()
    assert due.next_sync_at == now + timedelta(seconds=120)
    assert future.next_sync_at == now + timedelta(seconds=1)


@pytest.mark.django_db(transaction=True)
def test_enqueue_due_bridges_resets_claim_when_dispatch_fails(
    transactional_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker failure after DB claim makes the bridge due again."""

    del transactional_db
    now = timezone.now()

    def fail_send_task(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", fail_send_task)
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "queued-failure",
            model=SchedulerBridge,
            poll_interval=120,
            next_sync_at=now - timedelta(seconds=1),
        )

    with pytest.raises(RuntimeError, match="broker down"):
        enqueue_due_bridges(now=now)

    bridge.refresh_from_db()
    assert bridge.next_sync_at == now
    assert bridge.sync_stage == Bridge.SyncStage.IDLE
    assert bridge.sync_progress == {}


@pytest.mark.django_db
def test_enqueue_due_bridges_rejects_outer_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an outer transaction before scanning or claiming any bridges."""

    def unexpected_scan(**_kwargs: Any) -> None:
        raise AssertionError("Scheduler scanned before enforcing autocommit.")

    monkeypatch.setattr(integrate_scheduler, "models_with", unexpected_scan)
    with transaction.atomic(), pytest.raises(RuntimeError, match="requires autocommit"):
        enqueue_due_bridges()


@pytest.mark.django_db(transaction=True)
def test_scheduler_claims_a_row_before_running_it(
    transactional_db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight bridge's next poll is already pushed out before its sync runs.

    The claim is what an overlapping scan reads: a backfill outliving the tick
    cadence must be skipped, not double-synced, so the persisted ``next_sync_at``
    has to move out *before* the run rather than only when it records.
    """

    del transactional_db
    now = timezone.now()
    observed: list[Any] = []
    original_sync = SchedulerBridge.sync

    def observing_sync(self: SchedulerBridge) -> int:
        persisted = SchedulerBridge._base_manager.get(pk=self.pk)
        observed.append(persisted.next_sync_at)
        return original_sync(self)

    with system_context(reason="test integrate scheduler claim setup"):
        make_integration(
            "claimed",
            model=SchedulerBridge,
            poll_interval=120,
            next_sync_at=now - timedelta(seconds=1),
        )
    monkeypatch.setattr(SchedulerBridge, "sync", observing_sync)

    counters = _enqueue_and_run_due(now=now)

    assert counters == {"ran": 1, "errors": 0}
    assert observed == [now + timedelta(seconds=120)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL row locks")
def test_scheduled_admission_uses_the_schedulers_single_row_lock(transactional_db, monkeypatch):
    now = timezone.now()
    monkeypatch.setattr("angee.integrate.models.enqueue_task", lambda *args, **kwargs: None)
    with system_context(reason="test.scheduler.single_lock"):
        bridge = make_integration("single-admission-lock", model=SchedulerBridge, next_sync_at=now)
    with CaptureQueriesContext(connection) as queries:
        assert enqueue_due_bridges(now=now) == {"enqueued": 1, "skipped": 0}
    locks = [
        query["sql"] for query in queries
        if "FOR UPDATE" in query["sql"] and bridge._meta.db_table in query["sql"]
    ]
    assert len(locks) == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="Requires PostgreSQL skip-locked row semantics")
def test_scheduler_skips_row_locked_bridge_and_enqueues_other_due_bridges(transactional_db, monkeypatch):
    now = timezone.now()
    enqueued = []
    monkeypatch.setattr(SchedulerBridge, "request_sync", lambda self, **kwargs: enqueued.append(self.pk))
    with system_context(reason="test.scheduler.locked_rows"):
        locked = make_integration("locked-due", model=SchedulerBridge, next_sync_at=now)
        available = make_integration("available-due", model=SchedulerBridge, next_sync_at=now)

    def schedule():
        close_old_connections()
        try:
            # A regression must fail promptly, not hang behind this test's lock.
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '1s'")
            return enqueue_due_bridges(now=now)
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            SchedulerBridge._base_manager.lock_if_supported().get(pk=locked.pk)
            assert pool.submit(schedule).result(timeout=10) == {"enqueued": 1, "skipped": 1}
            assert enqueued == [available.pk]
            locked.refresh_from_db()
            assert locked.next_sync_at == now and locked.sync_stage == Bridge.SyncStage.IDLE

    assert enqueue_due_bridges(now=now) == {"enqueued": 1, "skipped": 0}
    assert enqueued == [available.pk, locked.pk]
