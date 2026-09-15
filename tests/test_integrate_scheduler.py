"""Tests for integrate registry discovery and bridge scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection, transaction
from django.test import override_settings
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.integrate import queue as integrate_queue
from angee.integrate import scheduler as integrate_scheduler
from angee.integrate import sync_runner as integrate_sync_runner
from angee.integrate import tasks as integrate_tasks
from angee.integrate.errors import IntegrationError
from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import (
    Bridge,
    BridgeSyncOccurrence,
    IntegrationLifecycle,
    IntegrationRuntimeStatus,
    SyncDispatchReceipt,
)
from angee.integrate.registry import bridge_models
from angee.integrate.scheduler import enqueue_due_bridges
from angee.integrate.sync import BridgeProgressReporter, current_bridge_progress
from angee.workflows.states import WorkflowPurpose
from angee.workflows_integrate.models import IntegrationWorkflowBridge
from angee.workflows_integrate.sync import (
    IntegrationSyncDefinition,
    IntegrationSyncOccurrence,
    _LaunchCapability,
    current_integration_sync_launch_capability,
)
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    Integration,
    _create_missing_tables,
    make_integration,
)
from tests.workflows import Step, Workflow, WorkflowRun


class SchedulerBridge(Bridge, Integration):
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
        if self.config.get("mode") == "refused":
            raise IntegrationError("Vendor refused the login for ada@example.com.")
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
        if self.config.get("detached_progress"):
            detached = type(self).objects.sudo(reason="test detached bridge progress").get(pk=self.pk)
            BridgeProgressReporter(detached).report(
                "discovering",
                message="Reached the sync budget",
                details={"items": items, "budget_exhausted": True, "cursor": {"seen": items}},
            )
        self.cursor = {"seen": items}
        return items

    def dispatch_sync(self, *, now: datetime) -> SyncDispatchReceipt:
        """Exercise the framework's asynchronous dispatch seam when requested."""

        if self.config.get("mode") != "async":
            return super().dispatch_sync(now=now)
        self.validate_sync_admission()
        receipt = SyncDispatchReceipt.dispatched(str(self.config["execution_ref"]))
        self.record_sync_dispatched(receipt, now=now)
        return receipt

    def sync_admission_generation(self) -> str:
        """Project the fixture's generation through the concrete bridge hook."""

        return str(self.config.get("generation", ""))

    def sync_execution_is_active(self, execution_ref: str) -> bool:
        """Resolve fixture execution liveness without treating its ref as proof."""

        return self.config.get("active_execution_ref") == execution_ref

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
    original = integrate_scheduler.queue_bridge_sync

    def run_queued(
        bridge: SchedulerBridge,
        *,
        now: datetime | None = None,
        persist: bool = True,
        occurrence: BridgeSyncOccurrence | None = None,
    ) -> None:
        nonlocal ran, errors
        assert persist is False
        assert now is not None
        assert occurrence is not None
        try:
            result = integrate_sync_runner.run_bridge_sync_job(
                bridge._meta.label_lower,
                bridge.pk,
                now.isoformat(),
                generation=str(bridge.sync_progress["queue_generation"]),
                occurrence=occurrence.canonical(),
                require_queue_token=True,
            )
        except Exception:
            ran += 1
            errors += 1
        else:
            if not result.get("skipped"):
                ran += 1

    integrate_scheduler.queue_bridge_sync = run_queued
    try:
        enqueue_due_bridges(now=now)
    finally:
        integrate_scheduler.queue_bridge_sync = original
    return {"ran": ran, "errors": errors}


def _queued_delivery(bridge: SchedulerBridge) -> dict[str, Any]:
    """Return the exact persisted queue token expected by the worker task."""

    bridge.refresh_from_db()
    return {
        "timestamp": str(bridge.sync_progress["queued_at"]),
        "generation": str(bridge.sync_progress["queue_generation"]),
        "occurrence": dict(bridge.sync_progress["queue_occurrence"]),
    }


@pytest.fixture(autouse=True)
def _scan_only_the_fixture_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scope the scheduler's model scan to this module's fixture bridge.

    The shared test registry accumulates concrete bridge models from other
    modules (posts' Feed, messaging's Channel) whose on-demand tables may not
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
def test_enqueued_due_bridges_run_only_due_rows(scheduler_tables: None) -> None:
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

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    due.refresh_from_db()
    future.refresh_from_db()
    unscheduled.refresh_from_db()
    assert due.cursor == {"seen": 2}
    assert future.cursor == {}
    assert unscheduled.cursor == {}


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_persists_success_telemetry(scheduler_tables: None) -> None:
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

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    integration.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_completed_at is not None
    assert bridge.last_sync_completed_at >= now
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 7
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_error == ""
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["items"] == 7
    assert bridge.last_sync_summary["items"] == 7
    assert bridge.cursor == {"seen": 7}
    assert bridge.next_sync_at == bridge.last_sync_completed_at + timedelta(seconds=42)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.OK
    assert integration.last_used_status == "ok"


@pytest.mark.django_db(transaction=True)
def test_paused_manual_sync_stays_one_shot_and_out_of_periodic_due_scan(
    scheduler_tables: None,
) -> None:
    """A paused bridge may sync explicitly without rearming periodic polling."""

    del scheduler_tables
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
        bridge._meta.label_lower, bridge.pk, now.isoformat(),
    )

    assert result == {"ok": True, "state": "completed", "items": 3, "skipped": False}
    bridge.refresh_from_db()
    assert bridge.cursor == {"seen": 3}
    assert bridge.next_sync_at is None
    with system_context(reason="test paused legacy due marker"):
        bridge.next_sync_at = now
        bridge.save(update_fields=["next_sync_at", "updated_at"])
        due = SchedulerBridge.objects.due_for_enqueue(
            timestamp=now, stale_before=now - timedelta(minutes=5),
        )
        assert not due.filter(pk=bridge.pk).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("recover_lost_delivery", [False, True])
def test_queued_paused_manual_sync_remains_one_shot(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    recover_lost_delivery: bool,
) -> None:
    """Explicit paused work survives delivery recovery without enabling polling."""

    del scheduler_tables
    now = timezone.now()
    first = now - timedelta(minutes=10) if recover_lost_delivery else now
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test queued paused manual sync"):
        bridge = make_integration(
            "queued-paused-manual",
            model=SchedulerBridge,
            lifecycle=IntegrationLifecycle.PAUSED,
            config={"items": 3},
        )
    integrate_queue.queue_bridge_sync(bridge, now=first)
    assert bridge.sync_progress["queue_lifecycle"] == "paused"
    if recover_lost_delivery:
        with system_context(reason="test lost paused manual delivery"):
            SchedulerBridge._base_manager.filter(pk=bridge.pk).update(updated_at=first)
        assert enqueue_due_bridges(now=now) == {"enqueued": 1, "skipped": 0}
        assert enqueued[-1]["occurrence"] == enqueued[0]["occurrence"]
        assert integrate_tasks.sync_bridge_now(**enqueued[0])["stale"] is True

    assert integrate_tasks.sync_bridge_now(**enqueued[-1]) == {
        "ok": True, "state": "completed", "items": 3, "skipped": False,
    }
    bridge.refresh_from_db()
    assert bridge.lifecycle == IntegrationLifecycle.PAUSED
    assert bridge.next_sync_at is None
    assert bridge.cursor == {"seen": 3}
    assert integrate_tasks.sync_bridge_now(**enqueued[-1])["stale"] is True
    assert enqueue_due_bridges(now=now + timedelta(days=1)) == {"enqueued": 0, "skipped": 0}


@pytest.mark.django_db(transaction=True)
def test_paused_manual_recovery_survives_broker_failure(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed recovery enqueue retains the one-shot and its bounded retry delay."""

    del scheduler_tables
    now = timezone.now()
    first = now - timedelta(minutes=10)
    with system_context(reason="test paused recovery broker failure"):
        bridge = make_integration(
            "paused-recovery-failure", model=SchedulerBridge, lifecycle=IntegrationLifecycle.PAUSED,
        )
        bridge.mark_sync_queued(now=first)
        original_occurrence = bridge.sync_progress["queue_occurrence"]
        SchedulerBridge._base_manager.filter(pk=bridge.pk).update(updated_at=first)

    def fail_enqueue(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(integrate_queue, "enqueue_task", fail_enqueue)
    with pytest.raises(RuntimeError, match="broker down"):
        enqueue_due_bridges(now=now)
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.next_sync_at is None
    assert bridge.sync_progress["queue_occurrence"] == original_occurrence
    assert enqueue_due_bridges(now=now + timedelta(minutes=1)) == {"enqueued": 0, "skipped": 0}

    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue, "enqueue_task", lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    assert enqueue_due_bridges(now=now + timedelta(minutes=6)) == {"enqueued": 1, "skipped": 0}
    assert enqueued[0]["occurrence"] == original_occurrence
    assert integrate_tasks.sync_bridge_now(**enqueued[0])["state"] == "completed"
    bridge.refresh_from_db()
    assert bridge.next_sync_at is None


@pytest.mark.django_db(transaction=True)
def test_paused_bridge_rejects_scheduled_admission_and_default_workflow_guard(
    scheduler_tables: None,
) -> None:
    """Manual one-shot support does not relax periodic or workflow eligibility."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test paused guarded admission"):
        bridge = make_integration("paused-guarded", model=SchedulerBridge, lifecycle=IntegrationLifecycle.PAUSED)
        scheduled = BridgeSyncOccurrence(
            kind="scheduled", key="scheduled:paused", occurred_at=now, window_key=now.isoformat(),
        )
        with pytest.raises(ValidationError, match="connected"):
            bridge.mark_sync_queued(now=now, occurrence=scheduled)
        with pytest.raises(ValidationError, match="connected"):
            bridge.validate_sync_admission()
        bridge.refresh_from_db()
        assert bridge.sync_stage == Bridge.SyncStage.IDLE
        assert bridge.next_sync_at is None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("blocked_state", ["disconnected", "terminal_error", "async_active"])
def test_manual_one_shot_does_not_bypass_other_admission_guards(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    blocked_state: str,
) -> None:
    """Explicit manual requests still respect configuration and execution guards."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: pytest.fail("queued blocked work"))
    with system_context(reason="test manual one-shot admission guards"):
        bridge = make_integration(
            f"manual-blocked-{blocked_state}", model=SchedulerBridge,
            lifecycle=(
                IntegrationLifecycle.DISCONNECTED if blocked_state == "disconnected" else IntegrationLifecycle.PAUSED
            ),
            runtime_status=(
                IntegrationRuntimeStatus.ERROR if blocked_state == "terminal_error" else IntegrationRuntimeStatus.OK
            ),
            sync_progress={"execution_ref": "workflow:active"} if blocked_state == "async_active" else {},
        )
    with pytest.raises(ValidationError):
        integrate_queue.queue_bridge_sync(bridge, now=now)
    assert integrate_sync_runner.run_bridge_sync_job(bridge._meta.label_lower, bridge.pk, now) == {
        "ok": True, "items": 0, "skipped": True, "ineligible": True,
    }


@pytest.mark.django_db(transaction=True)
def test_integration_error_reaches_sync_telemetry_verbatim(scheduler_tables: None) -> None:
    """An IntegrationError's operator-safe message is persisted; other failures stay generic."""

    del scheduler_tables
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
def test_a_recovered_sync_drops_the_previous_runs_error_marker(scheduler_tables: None) -> None:
    """A failed run's ``error`` never rides into the next run's completed marker."""

    del scheduler_tables
    first = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("recovered", model=SchedulerBridge, config={"mode": "refused"}, next_sync_at=first)
    assert _enqueue_and_run_due(now=first) == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    assert bridge.sync_progress["error"]

    second = bridge.next_sync_at
    assert second is not None
    with system_context(reason="test integrate scheduler recover"):
        bridge.config = {"items": 2}
        bridge.save(update_fields=["config"])
    assert _enqueue_and_run_due(now=second) == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_error == ""
    assert "error" not in bridge.sync_progress
    assert bridge.sync_progress["items"] == 2


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_records_errors_on_integration_runtime_status(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing syncs record bridge errors, reschedule, and push integration runtime status."""

    del scheduler_tables
    now = timezone.now()
    finished_at = now + timedelta(seconds=4)
    monkeypatch.setattr("angee.integrate.models.timezone.now", lambda: finished_at)
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
    assert bridge.last_sync_status == "error"
    assert bridge.sync_stage == Bridge.SyncStage.FAILED
    assert bridge.sync_error == "Integration operation failed."
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.FAILED
    assert bridge.sync_progress["error"] == "Integration operation failed."
    assert bridge.next_sync_at == finished_at + timedelta(seconds=17)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
    assert integration.runtime_status == IntegrationRuntimeStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "Integration operation failed."
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_enqueued_due_bridge_success_recovers_bridge_and_integration_runtime_status(scheduler_tables: None) -> None:
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
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 5
    assert bridge.next_sync_at == bridge.last_sync_completed_at + timedelta(seconds=23)
    assert integration.lifecycle == IntegrationLifecycle.CONNECTED
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

    result = _enqueue_and_run_due(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.COMPLETED
    assert bridge.sync_progress["details"] == {"items": 3, "source": "fixture"}
    assert bridge.sync_progress["message"] == "Scanning vendor rows"


@pytest.mark.django_db(transaction=True)
def test_sync_completion_uses_finish_time_and_keeps_detached_progress(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal marker keeps partition progress and records the actual finish."""

    del scheduler_tables
    queued_at = timezone.now()
    finished_at = queued_at + timedelta(seconds=17)
    monkeypatch.setattr("angee.integrate.models.timezone.now", lambda: finished_at)
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration(
            "detached-progress",
            model=SchedulerBridge,
            config={"items": 600, "detached_progress": True},
            next_sync_at=queued_at,
        )

    result = _enqueue_and_run_due(now=queued_at)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.last_sync_started_at == queued_at
    assert bridge.last_sync_completed_at == finished_at
    assert bridge.sync_progress == {
        "stage": Bridge.SyncStage.COMPLETED,
        "queued_at": queued_at.isoformat(),
        "queue_generation": "",
        "queue_lifecycle": "connected",
        "queue_occurrence": BridgeSyncOccurrence(
            kind="scheduled", key=f"scheduled:{queued_at.isoformat()}",
            occurred_at=queued_at, window_key=queued_at.isoformat(),
        ).canonical(),
        "execution_ref": "",
        "started_at": queued_at.isoformat(),
        "message": "Reached the sync budget",
        "details": {"items": 600, "budget_exhausted": True, "cursor": {"seen": 600}},
        "items": 600,
        "completed_at": finished_at.isoformat(),
    }


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TASK_LOCK_BACKEND="angee.jobs.locks.LocalLockBackend")
def test_declined_sync_run_releases_its_queue_claim(scheduler_tables: None) -> None:
    """A run that cannot take the lock clears its own claim, so recovery stops.

    The stale-queue sweep exists to repair a *lost* enqueue and cannot tell one from
    a declined run, so a claim left behind is re-queued every recovery window
    forever — which is what a live session's permanently-held lock produced.
    """

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("declined-run", model=SchedulerBridge)
        bridge.mark_sync_queued(now=now)

    # Hold the lock the way a live session does, then let a sync run decline.
    with bridge_advisory_lock(bridge):
        result = integrate_sync_runner.run_bridge_sync_job(
            bridge._meta.label_lower,
            bridge.pk,
            now.isoformat(),
            generation="",
        )

    assert result == {"ok": True, "items": 0, "skipped": True}
    with system_context(reason="test integrate scheduler verify"):
        bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.IDLE


@pytest.mark.django_db(transaction=True)
def test_declined_sync_run_never_clears_the_holder_stage(scheduler_tables: None) -> None:
    """The decliner releases only its own token — never a holder's live stage."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("declined-stale", model=SchedulerBridge)
        bridge.mark_sync_queued(now=now)
        # The holder moved the row on; this run's token is no longer current.
        bridge.mark_sync_started(now=now + timedelta(seconds=1))

    assert bridge.release_sync_queue(now=now) is False

    with system_context(reason="test integrate scheduler verify"):
        bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.SYNCING


@pytest.mark.django_db(transaction=True)
def test_sync_markers_keep_a_reporters_details(scheduler_tables: None) -> None:
    """Queueing a sync must not drop a live session's pairing report.

    ``sync_progress`` has disjoint owners: the scheduler owns the lifecycle marker,
    a reporter owns ``details``. Replacing the dict dropped the QR the operator was
    looking at when Sync was pressed mid-pairing.
    """

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("marker-merge", model=SchedulerBridge)
        BridgeProgressReporter(bridge).report(
            Bridge.SyncStage.SYNCING, details={"pairing": {"state": "awaiting_scan", "qr": "data:png"}}
        )

        bridge.mark_sync_queued(now=now)
        assert bridge.sync_progress["details"]["pairing"]["qr"] == "data:png"
        assert bridge.sync_progress["stage"] == Bridge.SyncStage.QUEUED
        assert bridge.sync_progress["queued_at"] == now.isoformat()

        bridge.mark_sync_started(now=now)
        assert bridge.sync_progress["details"]["pairing"]["qr"] == "data:png"
        assert bridge.sync_progress["stage"] == Bridge.SyncStage.SYNCING


@pytest.mark.django_db(transaction=True)
def test_progress_report_during_queued_stage_keeps_the_queue_marker(scheduler_tables: None) -> None:
    """A report firing while the row is QUEUED must not clobber the queue claim.

    The scheduler owns the ``QUEUED`` stage/marker; a reporter owns ``details``.
    A progress report arriving mid-queue (a live session publishing a pairing QR
    after Sync was pressed) merges its details without demoting the stage or
    dropping ``queued_at`` — otherwise the stale-queue recovery cannot tell a
    genuine queue claim from a run that has already advanced, and re-queues it
    forever. This pins the locked-merge guard in ``BridgeProgressReporter``.
    """

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-report", model=SchedulerBridge)
        bridge.mark_sync_queued(now=now)

        payload = BridgeProgressReporter(bridge).report(
            Bridge.SyncStage.SYNCING,
            message="Scanning vendor rows",
            details={"items": 3},
        )

    assert payload["stage"] == Bridge.SyncStage.QUEUED
    assert payload["queued_at"] == now.isoformat()
    assert payload["details"] == {"items": 3}
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED

    with system_context(reason="test integrate scheduler verify"):
        bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress["stage"] == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress["queued_at"] == now.isoformat()
    assert bridge.sync_progress["details"] == {"items": 3}
    assert bridge.sync_progress["message"] == "Scanning vendor rows"


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TASK_LOCK_BACKEND="angee.jobs.locks.LocalLockBackend")
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
@override_settings(ANGEE_TASK_LOCK_BACKEND="angee.jobs.locks.LocalLockBackend")
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

    assert result == {"ok": True, "state": "completed", "items": 6, "skipped": False}
    bridge.refresh_from_db()
    assert bridge.cursor == {"seen": 6}
    assert bridge.sync_stage == Bridge.SyncStage.COMPLETED


def test_sync_dispatch_receipts_reject_ambiguous_states() -> None:
    """The worker boundary cannot mistake malformed async work for completion."""

    with pytest.raises(ValueError, match="Unknown bridge sync dispatch state"):
        SyncDispatchReceipt(state="invalid", execution_ref="run:1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="completed bridge sync requires items"):
        SyncDispatchReceipt.completed(-1)
    with pytest.raises(ValueError, match="dispatched bridge sync requires only"):
        SyncDispatchReceipt(state="dispatched", items=0, execution_ref="run:1")
    with pytest.raises(ValueError, match="string execution reference"):
        SyncDispatchReceipt.dispatched(17)  # type: ignore[arg-type]


@pytest.mark.django_db(transaction=True)
def test_bridge_dispatch_can_return_durable_async_receipt(scheduler_tables: None) -> None:
    """Async dispatch remains distinct from completed item telemetry."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate async dispatch setup"):
        bridge = make_integration(
            "async-dispatch",
            model=SchedulerBridge,
            config={"mode": "async", "execution_ref": "workflow:17", "active_execution_ref": "workflow:17"},
        )

    result = integrate_tasks.run_bridge_sync_job(SchedulerBridge._meta.label_lower, bridge.pk, now.isoformat())

    assert result == {
        "ok": True,
        "state": "dispatched",
        "execution_ref": "workflow:17",
        "skipped": False,
    }
    bridge.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_completed_at is None
    assert bridge.last_sync_status == ""
    assert bridge.last_sync_items == 0
    assert bridge.sync_stage == Bridge.SyncStage.SYNCING
    assert bridge.sync_execution_ref == "workflow:17"
    assert bridge.next_sync_at is None


@pytest.mark.django_db(transaction=True)
@override_settings(ANGEE_TASK_LOCK_BACKEND="angee.jobs.locks.LocalLockBackend")
def test_effective_sync_stage_asks_durable_execution_owner(scheduler_tables: None) -> None:
    """An execution reference is useful only when its concrete owner says it is live."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate async stage setup"):
        bridge = make_integration(
            "async-stage",
            model=SchedulerBridge,
            config={"mode": "async", "execution_ref": "workflow:18", "active_execution_ref": "workflow:18"},
        )
        bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched("workflow:18"), now=now)

    with _cross_process_locks():
        assert bridge.effective_sync_stage == Bridge.SyncStage.SYNCING
        bridge.config = {"mode": "async", "execution_ref": "workflow:18"}
        assert bridge.effective_sync_stage == Bridge.SyncStage.FAILED


@pytest.mark.django_db(transaction=True)
def test_async_terminal_delivery_is_fenced_by_execution_reference(scheduler_tables: None) -> None:
    """Duplicate and late terminal callbacks cannot settle newer execution."""

    del scheduler_tables
    first = timezone.now()
    second = first + timedelta(seconds=1)
    with system_context(reason="test integrate async terminal setup"):
        bridge = make_integration("async-terminal", model=SchedulerBridge)
        bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched("workflow:A"), now=first)

    assert bridge.record_sync_terminal("workflow:A", now=second, result=4) is True
    assert bridge.record_sync_terminal("workflow:A", now=second, result=4) is False
    bridge.refresh_from_db()
    assert bridge.last_sync_items == 4
    assert bridge.sync_execution_ref == ""

    with system_context(reason="test integrate newer async execution"):
        bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched("workflow:B"), now=second)
    assert (
        bridge.record_sync_terminal(
            "workflow:A",
            now=second + timedelta(seconds=1),
            error=RuntimeError("late failure"),
        )
        is False
    )
    bridge.refresh_from_db()
    assert bridge.sync_execution_ref == "workflow:B"
    assert bridge.sync_stage == Bridge.SyncStage.SYNCING


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
    occurrence = BridgeSyncOccurrence(
        kind="manual",
        key=f"manual:{now.isoformat()}",
        occurred_at=now,
        window_key=now.isoformat(),
    ).canonical()

    assert enqueued == [
        (
            "integrate.sync_bridge_now",
            {
                "model_label": SchedulerBridge._meta.label_lower,
                "pk": bridge.pk,
                "timestamp": now.isoformat(),
                "generation": "",
                "occurrence": occurrence,
            },
        )
    ]
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_error == ""
    assert bridge.sync_progress == {
        "stage": Bridge.SyncStage.QUEUED,
        "queued_at": now.isoformat(),
        "queue_generation": "",
        "queue_occurrence": occurrence,
        "queue_lifecycle": "connected",
    }


@pytest.mark.django_db(transaction=True)
def test_queue_bridge_sync_refuses_active_async_execution(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manual request cannot overwrite telemetry for active async work."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate active async setup"):
        bridge = make_integration(
            "async-queue-refusal",
            model=SchedulerBridge,
            config={"active_execution_ref": "workflow:19"},
        )
        bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched("workflow:19"), now=now)

    with pytest.raises(ValidationError, match="already has asynchronous sync execution"):
        integrate_queue.queue_bridge_sync(bridge, now=now + timedelta(seconds=1))

    bridge.refresh_from_db()
    assert enqueued == []
    assert bridge.sync_stage == Bridge.SyncStage.SYNCING
    assert bridge.sync_execution_ref == "workflow:19"


@pytest.mark.django_db(transaction=True)
def test_sync_eligibility_preserves_active_execution_conflict_as_admission_only(
    scheduler_tables: None,
) -> None:
    """Retained execution owners can reuse lifecycle law without ignoring its own ref."""

    del scheduler_tables
    now = timezone.now()
    with system_context(reason="test integrate retained eligibility setup"):
        bridge = make_integration("retained-eligibility", model=SchedulerBridge)
        bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched("workflow:retained"), now=now)

    bridge.validate_sync_eligibility()
    with pytest.raises(ValidationError, match="already has asynchronous sync execution"):
        bridge.validate_sync_admission()


@pytest.mark.django_db(transaction=True)
def test_workflow_admission_pins_owner_on_canonical_row_inside_system_context(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The preflight sudo context cannot satisfy the execution principal's check."""

    del scheduler_tables
    with system_context(reason="test integrate workflow admission setup"):
        bridge = make_integration("workflow-owner-scope", model=SchedulerBridge)
        owner = bridge.owner
    call_command("rebac", "sync", verbosity=0)
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_binding",
        lambda self, *, definition: None,
        raising=False,
    )

    def validate_scope(self: SchedulerBridge, *, envelope: dict[str, Any]) -> None:
        del envelope
        actor, unscoped = self.effective_actor(strict=True)
        assert actor == to_subject_ref(owner)
        assert unscoped is False

    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_scope",
        validate_scope,
        raising=False,
    )
    definition = IntegrationSyncDefinition(key="odoo-sync", version_id=7, digest="a" * 64)
    envelope = {
        "owner_id": owner.pk,
        "configuration_generation": bridge.sync_admission_generation(),
        "credential": {"id": bridge.credential_id, "material_revision": bridge.credential.material_revision},
    }
    workflow = type("Workflow", (), {"pk": 7, "key": "odoo-sync"})()
    with system_context(reason="test integrate workflow admission"), transaction.atomic():
        db_connection = transaction.get_connection("default")
        def capability() -> _LaunchCapability:
            return _LaunchCapability(
                alias="default",
                connection_id=id(db_connection),
                atomic_id=id(db_connection.atomic_blocks[0]),
                bridge_model=SchedulerBridge,
                bridge_id=bridge.pk,
                actor_id=owner.pk,
                definition=definition,
                dedup_key="integration-sync:cycle",
                occurrence_id="poll:1",
                envelope=envelope,
            )

        denied = capability()
        monkeypatch.setattr(SchedulerBridge._meta, "rebac_resource_type", "mtidemo/parent")
        with pytest.raises(PermissionDenied):
            IntegrationWorkflowBridge._admit_integration_sync_workflow(
                bridge,
                denied,
                workflow=workflow,
                retained_run=None,
                actor=owner,
            )
        monkeypatch.setattr(SchedulerBridge._meta, "rebac_resource_type", "integrate/integration")
        admitted = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            admitted,
            workflow=workflow,
            retained_run=None,
            actor=owner,
        )

    assert admitted.locked_bridge.actor() == to_subject_ref(owner)
    assert admitted.disposition == "new"

    reference = "workflow-run:22"
    admitted.locked_bridge.record_sync_dispatched(SyncDispatchReceipt.dispatched(reference), now=timezone.now())
    retained = type("Run", (), {"pk": 22, "status": "running"})()
    with system_context(reason="test retained workflow admission"), transaction.atomic():
        active = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            active,
            workflow=workflow,
            retained_run=retained,
            actor=owner,
        )
    assert active.disposition == "active"

    retained.status = "succeeded"
    with system_context(reason="test terminal workflow admission"), transaction.atomic():
        terminal_pending = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            terminal_pending,
            workflow=workflow,
            retained_run=retained,
            actor=owner,
        )
    assert terminal_pending.disposition == "terminal_pending"
    assert terminal_pending.locked_bridge.record_sync_terminal(reference, now=timezone.now(), result=0) is True

    with system_context(reason="test settled workflow admission"), transaction.atomic():
        settled = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            settled,
            workflow=workflow,
            retained_run=retained,
            actor=owner,
        )
    assert settled.disposition == "settled"


@pytest.mark.django_db(transaction=True)
def test_workflow_admission_retains_exact_paused_manual_posture(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued manual one-shot remains eligible on new and retained launch."""

    del scheduler_tables
    now = timezone.now()
    occurrence = BridgeSyncOccurrence(
        kind="manual",
        key=f"manual:{now.isoformat()}",
        occurred_at=now,
        window_key=now.isoformat(),
    )
    with system_context(reason="test paused workflow admission setup"):
        bridge = make_integration(
            "workflow-paused-manual",
            model=SchedulerBridge,
            lifecycle=IntegrationLifecycle.PAUSED,
        )
        bridge.mark_sync_queued(now=now, occurrence=occurrence)
        owner = bridge.owner
    call_command("rebac", "sync", verbosity=0)
    monkeypatch.setattr(
        SchedulerBridge._meta,
        "rebac_resource_type",
        "integrate/integration",
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_binding",
        lambda self, *, definition: None,
        raising=False,
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_scope",
        lambda self, *, envelope: None,
        raising=False,
    )
    definition = IntegrationSyncDefinition(
        key="odoo-sync",
        version_id=7,
        digest="a" * 64,
    )
    envelope = {
        "owner_id": owner.pk,
        "configuration_generation": bridge.sync_admission_generation(),
        "credential": {
            "id": bridge.credential_id,
            "material_revision": bridge.credential.material_revision,
        },
        "occurrence": occurrence.canonical(),
        "queue_lifecycle": str(IntegrationLifecycle.PAUSED),
    }
    workflow = type("Workflow", (), {"pk": 7, "key": "odoo-sync"})()

    with system_context(reason="test paused workflow admission"), transaction.atomic():
        db_connection = transaction.get_connection("default")

        def capability(payload: dict[str, Any] = envelope) -> _LaunchCapability:
            return _LaunchCapability(
                alias="default",
                connection_id=id(db_connection),
                atomic_id=id(db_connection.atomic_blocks[0]),
                bridge_model=SchedulerBridge,
                bridge_id=bridge.pk,
                actor_id=owner.pk,
                definition=definition,
                dedup_key="integration-sync:manual",
                occurrence_id=occurrence.key,
                envelope=payload,
            )

        admitted = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            admitted,
            workflow=workflow,
            retained_run=None,
            actor=owner,
        )
        reference = "workflow-run:23"
        admitted.locked_bridge.record_sync_dispatched(
            SyncDispatchReceipt.dispatched(reference),
            now=now,
        )
        retained = type("Run", (), {"pk": 23, "status": "running"})()
        duplicate = capability()
        IntegrationWorkflowBridge._admit_integration_sync_workflow(
            bridge,
            duplicate,
            workflow=workflow,
            retained_run=retained,
            actor=owner,
        )

    assert admitted.disposition == "new"
    assert duplicate.disposition == "active"


@pytest.mark.django_db(transaction=True)
def test_workflow_admission_rejects_changed_paused_occurrence_or_lifecycle(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduled substitution and lifecycle drift cannot reuse a manual queue."""

    del scheduler_tables
    now = timezone.now()
    manual = BridgeSyncOccurrence(
        kind="manual",
        key=f"manual:{now.isoformat()}",
        occurred_at=now,
        window_key=now.isoformat(),
    )
    with system_context(reason="test paused workflow refusal setup"):
        bridge = make_integration(
            "workflow-paused-refusal",
            model=SchedulerBridge,
            lifecycle=IntegrationLifecycle.PAUSED,
        )
        bridge.mark_sync_queued(now=now, occurrence=manual)
        owner = bridge.owner
    call_command("rebac", "sync", verbosity=0)
    monkeypatch.setattr(
        SchedulerBridge._meta,
        "rebac_resource_type",
        "integrate/integration",
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_binding",
        lambda self, *, definition: None,
        raising=False,
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_scope",
        lambda self, *, envelope: None,
        raising=False,
    )
    definition = IntegrationSyncDefinition(
        key="odoo-sync",
        version_id=7,
        digest="a" * 64,
    )
    base_envelope = {
        "owner_id": owner.pk,
        "configuration_generation": bridge.sync_admission_generation(),
        "credential": {
            "id": bridge.credential_id,
            "material_revision": bridge.credential.material_revision,
        },
        "occurrence": manual.canonical(),
        "queue_lifecycle": str(IntegrationLifecycle.PAUSED),
    }
    workflow = type("Workflow", (), {"pk": 7, "key": "odoo-sync"})()

    def reject(envelope: dict[str, Any]) -> None:
        with system_context(reason="test paused workflow refusal"), transaction.atomic():
            db_connection = transaction.get_connection("default")
            capability = _LaunchCapability(
                alias="default",
                connection_id=id(db_connection),
                atomic_id=id(db_connection.atomic_blocks[0]),
                bridge_model=SchedulerBridge,
                bridge_id=bridge.pk,
                actor_id=owner.pk,
                definition=definition,
                dedup_key="integration-sync:manual",
                occurrence_id=manual.key,
                envelope=envelope,
            )
            IntegrationWorkflowBridge._admit_integration_sync_workflow(
                bridge,
                capability,
                workflow=workflow,
                retained_run=None,
                actor=owner,
            )

    scheduled = BridgeSyncOccurrence(
        kind="scheduled",
        key=manual.key,
        occurred_at=now,
        window_key=manual.window_key,
    )
    with pytest.raises(ValidationError, match="occurrence changed"):
        reject({**base_envelope, "occurrence": scheduled.canonical()})
    with pytest.raises(ValidationError, match="lifecycle changed"):
        reject({**base_envelope, "queue_lifecycle": None})

    with system_context(reason="test paused workflow lifecycle change"):
        bridge.connect()
    with pytest.raises(ValidationError, match="lifecycle changed"):
        reject(base_envelope)


@pytest.mark.django_db(transaction=True)
def test_connected_workflow_dispatch_retains_exact_legacy_envelope_on_duplicate(
    scheduler_tables: None,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connected retries reuse the exact pre-posture envelope and one Run."""

    del scheduler_tables, workflow_engine_tables, no_workflow_queue
    now = timezone.now()
    occurrence = BridgeSyncOccurrence(
        kind="scheduled",
        key=f"scheduled:{now.isoformat()}",
        occurred_at=now,
        window_key=now.isoformat(),
    )
    with system_context(reason="test connected retained workflow setup"):
        bridge = make_integration(
            "workflow-connected-retained",
            model=SchedulerBridge,
        )
        bridge.mark_sync_queued(now=now, occurrence=occurrence)
        head = Workflow.objects.create(
            key="integration-connected-retained",
            name="Integration connected retained",
            purpose=WorkflowPurpose.INTEGRATION_SYNC,
            max_steps=4,
            created_by=bridge.owner,
            updated_by=bridge.owner,
        )
        Step.objects.create(
            workflow=head,
            key="wait",
            name="Wait",
            step_class="wait",
            config={"until": "2099-01-01T00:00:00+00:00"},
            is_entry=True,
        )
        version = head.publish()
    call_command("rebac", "sync", verbosity=0)
    definition = IntegrationSyncDefinition(
        key=version.key,
        version_id=version.pk,
        digest=version.definition_digest(),
    )
    monkeypatch.setattr(
        SchedulerBridge._meta,
        "rebac_resource_type",
        "integrate/integration",
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "integration_sync_definition",
        lambda self: definition,
        raising=False,
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_binding",
        lambda self, *, definition: None,
        raising=False,
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "validate_integration_sync_scope",
        lambda self, *, envelope: None,
        raising=False,
    )
    monkeypatch.setattr(
        SchedulerBridge,
        "_admit_integration_sync_workflow",
        IntegrationWorkflowBridge._admit_integration_sync_workflow,
        raising=False,
    )

    def validate_run_launch(self: Workflow, **facts: Any) -> None:
        capability = current_integration_sync_launch_capability()
        assert capability is not None
        facts["subject"]._admit_integration_sync_workflow(
            capability,
            workflow=self,
            retained_run=facts["retained_run"],
            actor=facts["actor"],
        )

    monkeypatch.setattr(Workflow, "validate_run_launch", validate_run_launch)

    first = IntegrationWorkflowBridge.dispatch_workflow_sync(
        bridge,
        now=now,
        occurrence=IntegrationSyncOccurrence(
            kind="scheduled",
            key=occurrence.key,
            occurred_at=occurrence.occurred_at,
            window_key=occurrence.window_key,
        ),
    )
    second = IntegrationWorkflowBridge.dispatch_workflow_sync(
        bridge,
        now=now,
        occurrence=IntegrationSyncOccurrence(
            kind="scheduled",
            key=occurrence.key,
            occurred_at=occurrence.occurred_at,
            window_key=occurrence.window_key,
        ),
    )

    assert first.execution_ref == second.execution_ref
    with system_context(reason="test connected retained workflow result"):
        run = WorkflowRun.objects.get()
    assert "queue_lifecycle" not in run.input


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
    assert bridge.sync_progress == {
        "stage": Bridge.SyncStage.QUEUED,
        "queued_at": now.isoformat(),
        "queue_generation": "",
        "queue_occurrence": BridgeSyncOccurrence(
            kind="manual",
            key=f"manual:{now.isoformat()}",
            occurred_at=now,
            window_key=now.isoformat(),
        ).canonical(),
        "queue_lifecycle": "connected",
    }


@pytest.mark.django_db(transaction=True)
def test_stale_bridge_sync_task_payload_is_skipped(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A duplicate task that no longer matches queued state cannot sync again later."""

    del scheduler_tables
    first = timezone.now()
    second = first + timedelta(seconds=5)
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate scheduler setup"):
        bridge = make_integration("queued-stale", model=SchedulerBridge, config={"items": 9})

    integrate_queue.queue_bridge_sync(bridge, now=first)
    integrate_queue.queue_bridge_sync(bridge, now=second)

    stale = integrate_tasks.sync_bridge_now(**enqueued[0])

    assert stale == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.cursor == {}
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {
        "stage": Bridge.SyncStage.QUEUED,
        "queued_at": second.isoformat(),
        "queue_generation": "",
        "queue_occurrence": enqueued[1]["occurrence"],
        "queue_lifecycle": "connected",
    }


@pytest.mark.django_db(transaction=True)
def test_queued_bridge_generation_change_invalidates_worker(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker cannot dispatch configuration newer than its durable queue claim."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: None)
    with system_context(reason="test integrate generation setup"):
        bridge = make_integration(
            "queued-generation",
            model=SchedulerBridge,
            config={"generation": "one", "items": 9},
        )

    integrate_queue.queue_bridge_sync(bridge, now=now)
    delivery = _queued_delivery(bridge)
    with system_context(reason="test integrate generation change"):
        bridge.config = {"generation": "two", "items": 9}
        bridge.save(update_fields=["config", "updated_at"])

    result = integrate_tasks.sync_bridge_now(
        SchedulerBridge._meta.label_lower,
        bridge.pk,
        generation="one",
        timestamp=delivery["timestamp"],
        occurrence=delivery["occurrence"],
    )

    assert result == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.cursor == {}
    assert bridge.sync_stage == Bridge.SyncStage.IDLE


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("delivered_generation", [None, "wrong"])
def test_missing_or_wrong_queue_generation_cannot_claim_current_marker(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    delivered_generation: str | None,
) -> None:
    """Legacy and corrupt task payloads decline without clearing current work."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: None)
    with system_context(reason="test integrate malformed queue generation setup"):
        bridge = make_integration(
            f"queued-generation-{delivered_generation or 'missing'}",
            model=SchedulerBridge,
            config={"generation": "current", "items": 9},
        )
    integrate_queue.queue_bridge_sync(bridge, now=now)
    delivery = _queued_delivery(bridge)

    result = integrate_tasks.sync_bridge_now(
        SchedulerBridge._meta.label_lower,
        bridge.pk,
        delivery["timestamp"],
        generation=delivered_generation,
        occurrence=delivery["occurrence"],
    )

    assert result == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress["queue_generation"] == "current"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("delivery_kind", ["missing", "different"])
def test_missing_or_different_occurrence_cannot_claim_current_marker(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    delivery_kind: str,
) -> None:
    """A delivery cannot reconstruct or replace its immutable queue occurrence."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: None)
    with system_context(reason="test integrate malformed queue occurrence setup"):
        bridge = make_integration("queued-occurrence-fence", model=SchedulerBridge, config={"items": 9})
    integrate_queue.queue_bridge_sync(bridge, now=now)
    delivery = _queued_delivery(bridge)
    occurrence = None
    if delivery_kind == "different":
        occurrence = {
            **delivery["occurrence"],
            "key": "manual:different-issued-request",
        }

    result = integrate_tasks.sync_bridge_now(
        SchedulerBridge._meta.label_lower,
        bridge.pk,
        delivery["timestamp"],
        generation=delivery["generation"],
        occurrence=occurrence,
    )

    assert result == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress["queue_occurrence"] == delivery["occurrence"]


@pytest.mark.django_db(transaction=True)
def test_same_timestamp_new_generation_fences_old_delivery(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation makes queue identity safe when timestamps are reused."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate queue aba setup"):
        bridge = make_integration(
            "queued-generation-aba",
            model=SchedulerBridge,
            config={"generation": "one", "items": 9},
        )
    integrate_queue.queue_bridge_sync(bridge, now=now)
    with system_context(reason="test integrate queue aba change"):
        bridge.config = {"generation": "two", "items": 9}
        bridge.save(update_fields=["config", "updated_at"])
    integrate_queue.queue_bridge_sync(bridge, now=now)

    old = integrate_tasks.sync_bridge_now(**enqueued[0])
    assert old == {"ok": True, "items": 0, "skipped": True, "stale": True}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress["queue_generation"] == "two"

    current = integrate_tasks.sync_bridge_now(**enqueued[1])
    assert current == {"ok": True, "state": "completed", "items": 9, "skipped": False}


@pytest.mark.django_db(transaction=True)
def test_queued_bridge_paused_before_worker_is_not_dispatched(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker rechecks operator lifecycle after taking its advisory lock."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: None)
    with system_context(reason="test integrate pause setup"):
        bridge = make_integration("queued-pause", model=SchedulerBridge, config={"items": 9})
    integrate_queue.queue_bridge_sync(bridge, now=now)
    delivery = _queued_delivery(bridge)
    original_lock = integrate_sync_runner.bridge_advisory_lock

    @contextmanager
    def pause_after_lock(stale_bridge: SchedulerBridge) -> Iterator[bool]:
        with original_lock(stale_bridge) as acquired:
            with system_context(reason="test integrate pause after worker lock"):
                current = SchedulerBridge._base_manager.get(pk=stale_bridge.pk)
                current.pause()
            yield acquired

    monkeypatch.setattr(integrate_sync_runner, "bridge_advisory_lock", pause_after_lock)

    result = integrate_tasks.sync_bridge_now(
        SchedulerBridge._meta.label_lower,
        bridge.pk,
        **delivery,
    )

    assert result == {"ok": True, "items": 0, "skipped": True, "ineligible": True}
    bridge.refresh_from_db()
    assert bridge.cursor == {}
    assert bridge.sync_stage == Bridge.SyncStage.IDLE


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("legacy_marker", [False, True])
def test_stale_connected_queue_is_discarded_after_pause(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    legacy_marker: bool,
) -> None:
    """Recovery never promotes old connected work into a new paused one-shot."""

    del scheduler_tables
    now = timezone.now()
    first = now - timedelta(minutes=10)
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda *_args, **_kwargs: pytest.fail("requeued paused work"))
    with system_context(reason="test paused stale queue recovery"):
        bridge = make_integration("stale-then-paused", model=SchedulerBridge)
        bridge.mark_sync_queued(now=first)
        if legacy_marker:
            bridge.sync_progress.pop("queue_lifecycle")
            bridge.save(update_fields=["sync_progress", "updated_at"])
        bridge.pause()
        SchedulerBridge._base_manager.filter(pk=bridge.pk).update(updated_at=first)

    assert enqueue_due_bridges(now=now) == {"enqueued": 0, "skipped": 1}
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.IDLE
    assert "queue_lifecycle" not in bridge.sync_progress
    assert bridge.cursor == {}


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
        assert enqueued == []

    assert len(enqueued) == 1
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.QUEUED
    assert bridge.sync_progress == {
        "stage": Bridge.SyncStage.QUEUED,
        "queued_at": now.isoformat(),
        "queue_generation": "",
        "queue_occurrence": enqueued[0]["occurrence"],
        "queue_lifecycle": "connected",
    }


@pytest.mark.django_db(transaction=True)
def test_queue_bridge_sync_outer_rollback_does_not_publish_task(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller rollback discards both the queue marker and broker callback."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        integrate_queue,
        "enqueue_task",
        lambda _task_name, *, kwargs, **_options: enqueued.append(kwargs),
    )
    with system_context(reason="test integrate queue rollback setup"):
        bridge = make_integration("queued-outer-rollback", model=SchedulerBridge)
    prior_progress = dict(bridge.sync_progress)

    with pytest.raises(RuntimeError, match="rollback outer queue transaction"):
        with transaction.atomic():
            integrate_queue.queue_bridge_sync(bridge, now=now)
            assert enqueued == []
            raise RuntimeError("rollback outer queue transaction")

    assert enqueued == []
    bridge.refresh_from_db()
    assert bridge.sync_stage == Bridge.SyncStage.IDLE
    assert bridge.sync_progress == prior_progress


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
        occurrence: BridgeSyncOccurrence | None = None,
    ) -> None:
        assert persist is False
        assert occurrence is not None
        assert occurrence.kind == "scheduled"
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
def test_stale_queue_recovery_reuses_the_issued_occurrence(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker recovery republishes one cycle; it cannot manufacture a new window."""

    del scheduler_tables
    first = timezone.now() - timedelta(minutes=10)
    now = timezone.now()
    issued = BridgeSyncOccurrence(
        kind="manual",
        key="manual:retained-request",
        occurred_at=first,
        window_key="window:retained-request",
    )
    captured: list[BridgeSyncOccurrence] = []

    def capture(
        _bridge: SchedulerBridge,
        *,
        now: datetime | None = None,
        persist: bool = True,
        occurrence: BridgeSyncOccurrence | None = None,
    ) -> None:
        assert now is not None
        assert persist is False
        assert occurrence is not None
        captured.append(occurrence)

    monkeypatch.setattr(integrate_scheduler, "queue_bridge_sync", capture)
    with system_context(reason="test integrate stale occurrence setup"):
        bridge = make_integration("stale-occurrence", model=SchedulerBridge)
        bridge.mark_sync_queued(now=first, occurrence=issued)
        SchedulerBridge._base_manager.filter(pk=bridge.pk).update(updated_at=first)

    assert enqueue_due_bridges(now=now) == {"enqueued": 1, "skipped": 0}
    assert [occurrence.canonical() for occurrence in captured] == [issued.canonical()]
    bridge.refresh_from_db()
    assert bridge.sync_progress["queued_at"] == now.isoformat()
    assert bridge.sync_progress["queue_occurrence"] == issued.canonical()


@pytest.mark.django_db(transaction=True)
def test_scheduler_excludes_paused_and_disconnected_rows(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the operator's connected lifecycle participates in polling."""

    del scheduler_tables
    now = timezone.now()
    enqueued: list[int] = []
    monkeypatch.setattr(
        integrate_scheduler,
        "queue_bridge_sync",
        lambda bridge, **_kwargs: enqueued.append(bridge.pk),
    )
    with system_context(reason="test integrate lifecycle schedule setup"):
        paused = make_integration("due-paused", model=SchedulerBridge, next_sync_at=now)
        paused.pause()
        disconnected = make_integration("due-disconnected", model=SchedulerBridge, next_sync_at=now)
        disconnected.disconnect()

    assert enqueue_due_bridges(now=now) == {"enqueued": 0, "skipped": 0}
    assert enqueued == []


@pytest.mark.django_db(transaction=True)
def test_scheduler_validates_terminal_error_before_claiming(
    scheduler_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale terminal queue marker cannot manufacture retry eligibility."""

    del scheduler_tables
    now = timezone.now()
    monkeypatch.setattr(
        integrate_scheduler,
        "queue_bridge_sync",
        lambda *_args, **_kwargs: pytest.fail("terminal bridge was enqueued"),
    )
    with system_context(reason="test integrate terminal schedule setup"):
        bridge = make_integration("terminal-error", model=SchedulerBridge)
        bridge.record_sync_error(IntegrationError("Repair required."), now=now, retryable=False)
        bridge.sync_stage = Bridge.SyncStage.QUEUED
        bridge.sync_progress = {
            "stage": Bridge.SyncStage.QUEUED,
            "queued_at": (now - timedelta(minutes=10)).isoformat(),
            "queue_generation": "",
        }
        bridge.save(update_fields=["sync_stage", "sync_progress"])
        SchedulerBridge._base_manager.filter(pk=bridge.pk).update(updated_at=now - timedelta(minutes=10))

    assert enqueue_due_bridges(now=now) == {"enqueued": 0, "skipped": 1}
    bridge.refresh_from_db()
    assert bridge.next_sync_at is None
    assert bridge.sync_stage == Bridge.SyncStage.IDLE
    assert bridge.sync_progress == {"stage": Bridge.SyncStage.IDLE}


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

    counters = _enqueue_and_run_due(now=now)

    assert counters == {"ran": 1, "errors": 0}
    assert observed == [now + timedelta(seconds=120)]
