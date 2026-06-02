"""Tests for integrate registry discovery and bridge scheduling."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection
from django.utils import timezone
from rebac import system_context

from angee.iam.models import AccountStatus
from angee.integrate.models import Bridge, CapabilityStatus
from angee.integrate.registry import bridge_models, capability_models
from angee.integrate.scheduler import run_due_bridges
from tests.conftest import ExternalAccount, Vendor, _create_missing_tables


class SchedulerBridge(Bridge):
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


@pytest.fixture()
def scheduler_tables(transactional_db: Any) -> Iterator[None]:
    """Create the IAM and bridge tables required by scheduler tests."""

    del transactional_db
    created_iam_models = _create_missing_tables()
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
    account = _external_account("due-only")
    with system_context(reason="test integrate scheduler setup"):
        due = SchedulerBridge.objects.create(
            account=account,
            config={"items": 2},
            next_sync_at=now - timedelta(seconds=1),
        )
        future = SchedulerBridge.objects.create(
            account=account,
            config={"items": 3},
            next_sync_at=now + timedelta(seconds=1),
        )
        unscheduled = SchedulerBridge.objects.create(
            account=account,
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
    account = _external_account("success-telemetry")
    with system_context(reason="test integrate scheduler setup"):
        bridge = SchedulerBridge.objects.create(
            account=account,
            config={"items": 7},
            poll_interval=42,
            next_sync_at=now,
        )

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_completed_at == now
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 7
    assert bridge.cursor == {"seen": 7}
    assert bridge.next_sync_at == now + timedelta(seconds=42)


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_records_errors_and_rolls_up_account_status(scheduler_tables: None) -> None:
    """Failing syncs record bridge errors, reschedule, and push account status."""

    del scheduler_tables
    now = timezone.now()
    account = _external_account("error-rollup")
    with system_context(reason="test integrate scheduler setup"):
        bridge = SchedulerBridge.objects.create(
            account=account,
            config={"mode": "error"},
            poll_interval=17,
            next_sync_at=now,
        )

    result = run_due_bridges(now=now)

    assert result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    account.refresh_from_db()
    assert bridge.last_sync_started_at == now
    assert bridge.last_sync_status == "error"
    assert bridge.status == CapabilityStatus.ERROR
    assert bridge.last_error == "RuntimeError: vendor unavailable"
    assert bridge.last_error_at is not None
    assert bridge.next_sync_at == now + timedelta(seconds=17)
    assert account.status == AccountStatus.ERROR
    assert account.capability_statuses == {str(bridge.pk): AccountStatus.ERROR.value}
    assert account.last_error == "RuntimeError: vendor unavailable"
    assert account.last_error_at is not None
    assert account.last_used_at is not None


@pytest.mark.django_db(transaction=True)
def test_run_due_bridges_success_recovers_bridge_and_account_status(scheduler_tables: None) -> None:
    """A healthy sync after an error clears the bridge and account rollup."""

    del scheduler_tables
    first_now = timezone.now()
    account = _external_account("recovery")
    with system_context(reason="test integrate scheduler setup"):
        bridge = SchedulerBridge.objects.create(
            account=account,
            config={"mode": "error"},
            poll_interval=23,
            next_sync_at=first_now,
        )

    error_result = run_due_bridges(now=first_now)

    assert error_result == {"ran": 1, "errors": 1}
    bridge.refresh_from_db()
    account.refresh_from_db()
    assert bridge.status == CapabilityStatus.ERROR
    assert account.status == AccountStatus.ERROR

    second_now = first_now + timedelta(minutes=1)
    with system_context(reason="test integrate scheduler setup"):
        bridge.config = {"items": 5}
        bridge.next_sync_at = second_now
        bridge.save(update_fields=["config", "next_sync_at", "updated_at"])

    success_result = run_due_bridges(now=second_now)

    assert success_result == {"ran": 1, "errors": 0}
    bridge.refresh_from_db()
    account.refresh_from_db()
    assert bridge.status == CapabilityStatus.ACTIVE
    assert bridge.last_error == ""
    assert bridge.last_error_at is None
    assert bridge.last_sync_status == "ok"
    assert bridge.last_sync_items == 5
    assert bridge.next_sync_at == second_now + timedelta(seconds=23)
    assert account.status == AccountStatus.ACTIVE
    assert account.capability_statuses == {str(bridge.pk): AccountStatus.ACTIVE.value}
    assert account.last_error == ""
    assert account.last_error_at is None


@pytest.mark.django_db(transaction=True)
def test_external_account_rollup_tracks_capability_contributions(scheduler_tables: None) -> None:
    """One failing capability keeps the account in error until that contribution recovers."""

    del scheduler_tables
    account = _external_account("multi-capability")

    account.note_capability_status(
        capability_key="bridge-a",
        status=CapabilityStatus.ERROR,
        error="bridge-a failed",
    )
    account.note_capability_status(capability_key="bridge-b", status=CapabilityStatus.ACTIVE)
    account.refresh_from_db()

    assert account.capability_statuses == {
        "bridge-a": AccountStatus.ERROR.value,
        "bridge-b": AccountStatus.ACTIVE.value,
    }
    assert account.status == AccountStatus.ERROR
    assert account.last_error == "bridge-a failed"

    account.note_capability_status(capability_key="bridge-b", status=CapabilityStatus.ACTIVE)
    account.refresh_from_db()

    assert account.status == AccountStatus.ERROR
    assert account.last_error == "bridge-a failed"

    account.note_capability_status(capability_key="bridge-a", status=CapabilityStatus.ACTIVE)
    account.refresh_from_db()

    assert account.capability_statuses == {
        "bridge-a": AccountStatus.ACTIVE.value,
        "bridge-b": AccountStatus.ACTIVE.value,
    }
    assert account.status == AccountStatus.ACTIVE
    assert account.last_error == ""
    assert account.last_error_at is None


@pytest.mark.django_db(transaction=True)
def test_integrate_registry_discovers_bridge_models_in_deterministic_order(scheduler_tables: None) -> None:
    """Registry helpers include the concrete fixture and sort by model label."""

    del scheduler_tables

    discovered_bridge_models = bridge_models()
    discovered_capability_models = capability_models()
    bridge_labels = tuple(model._meta.label_lower for model in discovered_bridge_models)
    capability_labels = tuple(model._meta.label_lower for model in discovered_capability_models)

    assert SchedulerBridge in discovered_bridge_models
    assert SchedulerBridge in discovered_capability_models
    assert bridge_labels == tuple(sorted(bridge_labels))
    assert capability_labels == tuple(sorted(capability_labels))


def _external_account(slug: str) -> ExternalAccount:
    """Create one vendor and linked external account for a scheduler test."""

    with system_context(reason="test integrate scheduler setup"):
        vendor = Vendor.objects.create(slug=slug, display_name=slug.title())
        return ExternalAccount.objects.create(vendor=vendor, external_id=f"{slug}-external")
