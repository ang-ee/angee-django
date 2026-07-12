"""Tests for integrate registry discovery and bridge scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate import queue as integrate_queue
from angee.integrate import scheduler as integrate_scheduler
from angee.integrate import tasks as integrate_tasks
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import Bridge, IntegrationLifecycle, IntegrationRuntimeStatus
from angee.integrate.registry import bridge_models
from angee.integrate.scheduler import enqueue_due_bridges, run_due_bridges
from angee.integrate.sync import current_bridge_progress
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Integration,
    _create_missing_tables,
    make_integration,
)


class SchedulerBridge(Integration, Bridge):
    """Concrete bridge fixture driven by the integrate scheduler tests."""

    class Meta(Bridge.Meta):
        """Django model options for the scheduler bridge fixture."""

        abstract = False
        app_label = "integrate"
        db_table = "test_integrate_scheduler_bridge"
        rebac_resource_type = "tests/scheduler_bridge"
        rebac_id_attr = "sqid"

    def sync(self) -> int:
        """Pretend to synchronize vendor rows and persist a cursor."""

        if self.config.get("mode") == "error":
            raise RuntimeError("vendor unavailable")
        items = int(self.config.get("items", 1))
        if self.config.get("assert_locked"):
            assert self.is_syncing is True
        if self.config.get("progress"):
            reporter = current_bridge_progress()
            assert reporter is not None
            reporter.report(
                "discovering",
                message="Scanning vendor rows",
                details={"items": items, "source": "fixture"},
            )
        self.cursor = {"seen": items}
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


@pytest.fixture(autouse=True)
def _scan_only_the_fixture_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope the scheduler's model scan to this module's fixture bridge.

    The shared test registry accumulates concrete bridge models from other
    modules (social's Feed, messaging's Channel) whose on-demand tables may not
    exist in this session, so an unscoped scan fails on table/relation state
    these tests don't own. Cross-model discovery itself is covered by
    ``test_integrate_registry_discovers_bridge_models_in_deterministic_order``,
    which reads the registry without querying rows.
    """

    monkeypatch.setattr(integrate_scheduler, "bridge_models", lambda base: (SchedulerBridge,))


@pytest.fixture()
def scheduler_tables(transactional_db: Any) -> Iterator[None]:
    """Create the IAM and bridge tables required by scheduler tests."""

    del transactional_db
    created_iam_models = _create_missing_tables(IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS)
    bridge_created = False
    if SchedulerBridge._meta.db_table not in connection.introspection.table_names():
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(SchedulerBridge)
        bridge_created = True

    try:
        yield
    finally:
        if bridge_created:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(SchedulerBridge)
        if created_iam_models:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created_iam_models):
                    schema_editor.delete_model(model)


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_runs_only_due_rows(scheduler_tables: None) -> None:
    """The scheduler runs due rows and skips future or unscheduled rows."""

    del scheduler_tables
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

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 0}
    due.refresh_from_db()
    future.refresh_from_db()
    unscheduled.refresh_from_db()
    assert due.cursor == {"seen": 2}
    assert future.cursor == {}
    assert unscheduled.cursor == {}


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_persists_success_telemetry(scheduler_tables: None) -> None:
    """Successful syncs persist scheduler telemetry, cursor, count, and next run."""

    del scheduler_tables
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

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_completed_at == now
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 7
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_error == ""
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["items"] == 7
    assert bridge.last_sync_summary["items"] == 7
    assert bridge.cursor == {"seen": 7}
    assert bridge.next_sync_at == now + timedelta(seconds=42)
    assert integration.lifecycle == IntegrationLifecycle.ACTIVE
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_records_errors_on_integration_runtime_status(scheduler_tables: None) -> None:
    """Failing syncs record bridge errors, reschedule, and push integration runtime status."""

    del scheduler_tables
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

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_status == "error"
    assert bridge.sync_stage == Bridge.SyncStage.FAILED
    assert bridge.sync_error == "RuntimeError: vendor unavailable"
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.FAILED
    assert bridge.sync_progress["error"] == "RuntimeError: vendor unavailable"
    assert bridge.next_sync_at == now + timedelta(seconds=17)
    assert integration.lifecycle == IntegrationLifecycle.ACTIVE
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "RuntimeError: vendor unavailable"
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_success_recovers_bridge_and_integration_runtime_status(scheduler_tables: None) -> None:
    """A healthy sync after an error clears the integration runtime status."""

    del scheduler_tables
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

    error_result = run_due_bridges(now=first_now)

    assert error_result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR

    second_now = first_now + timedelta(minutes=1)
    with system_context(reason="test integrate scheduler setup"):
        bridge.config = {"items": 5}
        bridge.next_sync_at = second_now
        bridge.save(update_fields=["config", "next_sync_at", "updated_at"])

    success_result = run_due_bridges(now=second_now)

    assert success_result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 5
    assert bridge.next_sync_at == second_now + timedelta(seconds=23)
    assert integration.lifecycle == IntegrationLifecycle.ACTIVE
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"
    assert integration.last_error == ""
    assert integration.last_error_at is None


@pytest.mark.django_db(transaction=True)
def test_bridge_progress_reporter_persists_progress_payload(scheduler_tables: None) -> None:
    """Bridge.sync can publish generic progress without knowing the storage fields."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "progress-payload",
            model=SchedulerBridge,
            config={"items": 3, "progress": True},
            next_sync_at=now,
        )

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["details"] == {"items": 3, "source": "fixture"}
    assert bridge.sync_progress["message"] == "Scanning vendor rows"


@pytest.mark.django_db(transaction=True)
def test_bridge_is_syncing_uses_live_lock_state(scheduler_tables: None) -> None:
    """The live lock is separate from durable stage telemetry."""

    del scheduler_tables
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("live-lock", model=SchedulerBridge)

    assert bridge.is_syncing is False

    with bridge_advisory_lock(bridge) as acquired:
        assert acquired is True
        assert bridge.is_syncing is True
        with bridge_advisory_lock(bridge) as second_acquired:
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


@pytest.mark.django_db(transaction=True)
def test_effective_sync_stage_reconciles_stale_records_against_the_lock(scheduler_tables: None) -> None:
    """A live-ish persisted stage without the live lock reads as FAILED.

    The persisted ``sync_stage`` is a progress report a crashed worker leaves
    stale; the effective projection trusts the advisory lock instead, so the UI
    never shows a phantom "syncing". ``queued`` legitimately holds no lock (the
    task has not started) and passes through, as do the terminal stages. The
    reconciliation only applies when the lock backend is cross-process — a
    process-local backend (this suite's) cannot see a worker's lock, so the
    column is trusted as-is there.
    """

    del scheduler_tables
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("stale-stage", model=SchedulerBridge)

        # The suite's LocalLockBackend is process-local: no reconciliation.
        bridge.sync_stage = bridge.SyncStage.SYNCING
        assert bridge.effective_sync_stage == bridge.SyncStage.SYNCING

    with _cross_process_locks():
        bridge.sync_stage = bridge.SyncStage.SYNCING
        assert bridge.effective_sync_stage == bridge.SyncStage.FAILED
        with bridge_advisory_lock(bridge) as acquired:
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


@pytest.mark.django_db(transaction=True)
def test_run_bridge_sync_job_holds_the_live_lock(scheduler_tables: None) -> None:
    """The queued task runner owns the live lock while a bridge sync runs."""

    del scheduler_tables
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
    assert bridge.cursor == {"seen": 6}
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED


@pytest.mark.django_db(transaction=True)
def test_queue_bridge_sync_marks_queued_and_defers_task(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queueing persists visible state before the worker picks up the task."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[tuple[str, dict[str, Any]]] = []

    def fake_enqueue_task(task_name: str, *, kwargs: dict[str, Any], **options: Any) -> None:
        assert options == {}
        enqueued.append((task_name, kwargs))

    monkeypatch.setattr(integrate_queue, "enqueue_task", fake_enqueue_task)
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-bridge", model=SchedulerBridge)

    integrate_queue.queue_bridge_sync(bridge, now=now)

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
    assert bridge.sync_progress == {"stage": Bridge.SyncStage.QUEUED, "queued_at": now.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_queue_bridge_sync_can_enqueue_duplicate_requests(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task-level locks, not the broker, own duplicate execution exclusion."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-duplicate", model=SchedulerBridge)

    integrate_queue.queue_bridge_sync(bridge, now=now)
    integrate_queue.queue_bridge_sync(bridge, now=now)

    assert len(enqueued) == 2
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"stage": Bridge.SyncStage.QUEUED, "queued_at": now.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_stale_bridge_sync_task_payload_is_skipped(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate task that no longer matches queued state cannot sync again later."""

    del scheduler_tables
    first = timezone.now()
    second = first + timedelta(seconds=5)
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: None,
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-stale", model=SchedulerBridge, config={"items": 9})

    integrate_queue.queue_bridge_sync(bridge, now=first)
    integrate_queue.queue_bridge_sync(bridge, now=second)

    stale = integrate_tasks.sync_bridge_now(SchedulerBridge._meta.label_lower, bridge.pk, first.isoformat())

    assert stale == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.cursor == {}
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"stage": Bridge.SyncStage.QUEUED, "queued_at": second.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_queue_bridge_sync_inside_outer_transaction_preserves_caller(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queueing a bridge sync does not poison the caller's outer transaction."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-db-duplicate", model=SchedulerBridge)

    with transaction.atomic():
        integrate_queue.queue_bridge_sync(bridge, now=now)
        assert SchedulerBridge._base_manager.filter(pk=bridge.pk).exists()

    assert len(enqueued) == 1
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {"stage": Bridge.SyncStage.QUEUED, "queued_at": now.isoformat()}


@pytest.mark.django_db(transaction=True)
def test_integrate_registry_discovers_bridge_models_in_deterministic_order(scheduler_tables: None) -> None:
    """Registry helpers include the concrete fixture and sort by model label."""

    del scheduler_tables

    discovered_bridge_models = bridge_models(Bridge)
    bridge_labels = tuple(model._meta.label_lower for model in discovered_bridge_models)

    assert SchedulerBridge in discovered_bridge_models
    assert bridge_labels == tuple(sorted(bridge_labels))


def test_periodic_task_drives_the_due_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """The queue tick calls the pure due-scan; registration is by stable task name."""

    calls: list[bool] = []
    monkeypatch.setattr(integrate_tasks.scheduler, "enqueue_due_bridges", lambda: calls.append(True))
    integrate_tasks.sync_due_bridges()

    assert calls == [True]
    assert integrate_tasks.sync_due_bridges.name == "integrate.sync_due_bridges"


@pytest.mark.django_db(transaction=True)
def test_enqueue_due_bridges_claims_and_queues_rows(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The periodic scan claims due rows and enqueues bridge work."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[tuple[int, datetime | None]] = []

    def fake_queue_bridge_sync(
        bridge: SchedulerBridge,
        *,
        now: datetime | None = None,
        persist: bool = True,
    ) -> None:
        assert persist is False
        enqueued.append((bridge.pk, now))

    monkeypatch.setattr(integrate_scheduler, "queue_bridge_sync", fake_queue_bridge_sync)
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
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broker failure after DB claim makes the bridge due again."""

    del scheduler_tables
    now = timezone.now()

    def fail_queue_bridge_sync(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(integrate_scheduler, "queue_bridge_sync", fail_queue_bridge_sync)
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


@pytest.mark.django_db(transaction=True)
def test_scheduler_claims_a_row_before_running_it(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-flight bridge's next poll is already pushed out before its sync runs.

    The claim is what an overlapping scan reads: a backfill outliving the tick
    cadence must be skipped, not double-synced, so the persisted ``next_sync_at``
    has to move out *before* the run rather than only when it records.
    """

    del scheduler_tables
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

    counters = run_due_bridges(now=now)

    assert counters == {"ran": 1, "errors": 0}
    assert observed == [now + timedelta(seconds=120)]
