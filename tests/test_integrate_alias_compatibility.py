"""Regression contracts for routed owners dispatching legacy integration hooks."""

from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import router, transaction
from django.utils import timezone
from django.utils.connection import ConnectionDoesNotExist
from rebac import system_context

from angee.integrate import queue as integrate_queue
from angee.integrate.credentials import CredentialKind, OAuthCredentialHandler
from angee.integrate.impl import LiveBridgeImpl
from angee.integrate.live import PairingState
from angee.integrate.models import Bridge, IntegrationRuntimeStatus
from angee.integrate.session import LiveSession
from angee.integrate.session_runner import run_bridge_session_job
from angee.integrate.sync_runner import run_bridge_sync_job
from tests.conftest import Credential, Integration, make_integration
from tests.test_integrate_scheduler import (
    SchedulerBridge,
)
from tests.test_integrate_scheduler import (
    scheduler_alias as scheduler_alias,
)
from tests.test_integrate_scheduler import (
    scheduler_tables as scheduler_tables,
)
from tests.test_transitions import TransitionRouter


@pytest.mark.parametrize("worker", ["sync", "session"])
def test_dequeued_unknown_alias_fails_before_model_resolution(worker: str) -> None:
    """A bad payload alias raises natively before malformed labels can be skipped."""

    with pytest.raises(ConnectionDoesNotExist, match="missing_integrate_worker_database"):
        if worker == "sync":
            run_bridge_sync_job("invalid-model-label", 1, using="missing_integrate_worker_database")
        else:
            run_bridge_session_job(
                "invalid-model-label",
                1,
                stop_event=threading.Event(),
                using="missing_integrate_worker_database",
            )


@pytest.mark.django_db(transaction=True)
def test_legacy_credential_handler_refresh_retains_selected_alias(
    scheduler_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy handler signatures can mutate material after the refresh row lock."""

    with system_context(reason="test legacy credential setup"):
        bridge = make_integration(
            "legacy-refresh", model=SchedulerBridge, kind=CredentialKind.OAUTH, material={"access_token": "old"}
        )
        credential = Credential.objects.using(scheduler_alias).get(pk=bridge.credential_id)
    calls: list[str] = []

    def can_refresh(_handler: OAuthCredentialHandler, row: Any) -> bool:
        assert row._state.db == scheduler_alias
        calls.append("can_refresh")
        return True

    def refresh(_handler: OAuthCredentialHandler, row: Any) -> None:
        assert row._state.db == scheduler_alias
        calls.append("refresh")
        row.update_material(access_token="renewed")

    monkeypatch.setattr(OAuthCredentialHandler, "can_refresh", can_refresh)
    monkeypatch.setattr(OAuthCredentialHandler, "refresh", refresh)
    credential._state.db = "wrong_instance"
    routing = TransitionRouter("wrong_writer")
    with monkeypatch.context() as patch, system_context(reason="test legacy credential refresh"):
        patch.setattr(router, "routers", [routing])
        credential.refresh_now(using=scheduler_alias)
        assert credential.reveal()["access_token"] == "renewed"
        assert Credential.objects.using(scheduler_alias).get(pk=credential.pk).reveal()["access_token"] == "renewed"
    assert calls == ["can_refresh", "refresh"]
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_legacy_revoke_callback_captures_alias_before_commit(
    scheduler_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A deferred legacy hook retains its own affinity if the caller later rebinds."""

    with system_context(reason="test legacy revoke setup"):
        bridge = make_integration("legacy-revoke", model=SchedulerBridge)
        credential = Credential.objects.using(scheduler_alias).get(pk=bridge.credential_id)
    revoked: list[str] = []

    def check_disconnect(row: Any) -> None:
        assert row._state.db == scheduler_alias

    def revoke_remote(row: Any) -> None:
        assert row._state.db == scheduler_alias
        row.update_material(api_key="revoked")
        revoked.append(row._state.db)

    monkeypatch.setattr(Credential, "check_disconnect", check_disconnect)
    monkeypatch.setattr(Credential, "revoke_remote", revoke_remote)
    routing = TransitionRouter("wrong_writer")
    with monkeypatch.context() as patch, system_context(reason="test legacy revoke"):
        patch.setattr(router, "routers", [routing])
        with transaction.atomic(using=scheduler_alias):
            Credential.objects.prepare_disconnect(credential, using=scheduler_alias)
            assert revoked == []
            credential._state.db = "wrong_instance"
        assert revoked == [scheduler_alias]
        assert Credential.objects.using(scheduler_alias).get(pk=credential.pk).reveal()["api_key"] == "revoked"
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_legacy_queue_sync_and_status_hooks_keep_worker_alias(
    scheduler_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old hook signatures keep working through queue admission and completion writes."""

    with system_context(reason="test legacy bridge setup"):
        bridge = make_integration("legacy-sync", model=SchedulerBridge)
    hooks: list[str] = []
    payloads: list[dict[str, Any]] = []

    def mark_sync_queued(row: SchedulerBridge, *, now: datetime) -> None:
        assert row._state.db == scheduler_alias
        hooks.append("queued")
        Bridge.mark_sync_queued(row, now=now)

    def sync(row: SchedulerBridge) -> int:
        assert row._state.db == scheduler_alias
        hooks.append("sync")
        row.cursor = {"legacy": True}
        return 2

    def report_status(row: SchedulerBridge, status: Any, error: Any = "") -> None:
        assert row._state.db == scheduler_alias
        hooks.append("status")
        Integration.report_status(row, status, error)

    monkeypatch.setattr(SchedulerBridge, "mark_sync_queued", mark_sync_queued)
    monkeypatch.setattr(SchedulerBridge, "sync", sync)
    monkeypatch.setattr(SchedulerBridge, "report_status", report_status)
    monkeypatch.setattr(integrate_queue, "enqueue_task", lambda _task, *, kwargs: payloads.append(kwargs))
    bridge._state.db = "wrong_instance"
    routing = TransitionRouter("wrong_writer")
    with monkeypatch.context() as patch, system_context(reason="test legacy queued bridge"):
        patch.setattr(router, "routers", [routing])
        integrate_queue.queue_bridge_sync(bridge, now=timezone.now(), using=scheduler_alias)
        assert run_bridge_sync_job(**payloads[0], require_queue_token=True)["items"] == 2
        stored = SchedulerBridge.objects.using(scheduler_alias).get(pk=bridge.pk)
        assert stored.cursor == {"legacy": True}
        assert stored.runtime_status == IntegrationRuntimeStatus.OK
        assert stored.last_sync_items == 2
    assert hooks == ["queued", "sync", "status"]
    assert routing.writes == []


@pytest.mark.django_db(transaction=True)
def test_live_impl_dispatches_legacy_account_release_on_selected_alias(
    scheduler_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy bridge hook still reaches its locked state update and reload."""

    with system_context(reason="test legacy live setup"):
        bridge = make_integration(
            "legacy-live-release", model=SchedulerBridge, subscription_state={"own_id": "claimed", "desired": "live"}
        )

    def release_live_account(row: SchedulerBridge, *, identity_key: str, desired: Any) -> None:
        assert row._state.db == scheduler_alias
        Bridge.release_live_account(row, identity_key=identity_key, desired=desired)

    monkeypatch.setattr(SchedulerBridge, "release_live_account", release_live_account)
    bridge._state.db = "wrong_instance"
    routing = TransitionRouter("wrong_writer")
    with monkeypatch.context() as patch, system_context(reason="test legacy live release"):
        patch.setattr(router, "routers", [routing])
        LiveBridgeImpl(bridge).release_account(desired=Bridge.LiveState.STOPPED, using=scheduler_alias)
        stored = SchedulerBridge.objects.using(scheduler_alias).get(pk=bridge.pk)
        assert stored.subscription_state == {"desired": "stopped"}
    assert routing.writes == []


def test_transport_only_live_session_accepts_report_only_reporter() -> None:
    """Transport setup and reporting require no model state or reporter database API."""

    reports: list[tuple[str, Any]] = []
    bridge = SimpleNamespace(
        live_impl=SimpleNamespace(state_identity_key="own_id"),
        subscription_state={},
        SyncStage=Bridge.SyncStage,
    )
    reporter = SimpleNamespace(report=lambda stage, *, details: reports.append((stage, details)))
    session = LiveSession(bridge, reporter=reporter, stop_event=threading.Event())
    session._report(PairingState.STARTING)
    assert reports == [(Bridge.SyncStage.DISCOVERING, {"pairing": {"state": PairingState.STARTING}})]


@pytest.mark.django_db(transaction=True)
def test_live_session_with_report_only_reporter_uses_bound_bridge_for_persistence(
    scheduler_alias: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first credential read selects from model affinity independently of reporting."""

    with system_context(reason="test report-only session setup"):
        bridge = make_integration("report-only-session", model=SchedulerBridge)
        bridge = SchedulerBridge.objects.using(scheduler_alias).get(pk=bridge.pk)
    monkeypatch.setattr(
        SchedulerBridge,
        "live_impl",
        property(lambda _bridge: SimpleNamespace(state_identity_key="own_id")),
    )
    session = LiveSession(
        bridge,
        reporter=SimpleNamespace(report=lambda _stage, **_kwargs: None),
        stop_event=threading.Event(),
    )
    routing = TransitionRouter("wrong_writer")
    with monkeypatch.context() as patch, system_context(reason="test report-only session persistence"):
        patch.setattr(router, "routers", [routing])
        credential = session._fresh_credential()
        assert credential is not None
        credential.update_material(api_key="session-wrote")
        assert session.using == scheduler_alias
        assert Credential.objects.using(scheduler_alias).get(pk=credential.pk).reveal()["api_key"] == "session-wrote"
    assert routing.writes == []
