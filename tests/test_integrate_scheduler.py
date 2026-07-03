"""Tests for integrate registry discovery and bridge scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app
from rebac import system_context

from angee.integrate import scheduler as integrate_scheduler
from angee.integrate import tasks as integrate_tasks
from angee.integrate.models import Bridge, IntegrationStatus
from angee.integrate.registry import bridge_models
from angee.integrate.scheduler import run_due_bridges
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
    assert bridge.cursor == {"seen": 7}
    assert bridge.next_sync_at == now + timedelta(seconds=42)
    assert integration.status == IntegrationStatus.ACTIVE
    assert integration.last_used_status == "active"


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_records_errors_on_integration_status(scheduler_tables: None) -> None:
    """Failing syncs record bridge errors, reschedule, and push integration status."""

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
    assert bridge.next_sync_at == now + timedelta(seconds=17)
    assert integration.status == IntegrationStatus.ERROR
    assert integration.last_used_status == "error"
    assert integration.last_error == "RuntimeError: vendor unavailable"
    assert integration.last_error_at is not None
    assert integration.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_success_recovers_bridge_and_integration_status(scheduler_tables: None) -> None:
    """A healthy sync after an error clears the integration status."""

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
    assert integration.status == IntegrationStatus.ERROR

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
    assert integration.status == IntegrationStatus.ACTIVE
    assert integration.last_used_status == "active"
    assert integration.last_error == ""
    assert integration.last_error_at is None


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
    monkeypatch.setattr(integrate_tasks.scheduler, "run_due_bridges", lambda: calls.append(True))
    integrate_tasks.sync_due_bridges(0)

    assert calls == [True]
    assert "integrate.sync_due_bridges" in procrastinate_app.tasks


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
