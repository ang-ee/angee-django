"""Smoke tests for the framework jobs app."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import Mock

import pytest
from django.apps import apps
from django.db import transaction

from angee.jobs.enqueue import enqueue_task
from angee.jobs.timeouts import task_time_budget


@pytest.mark.parametrize("soft,hard,seconds", [(840, 900, 810), (180, 120, 90), (30, 60, 0)])
def test_task_time_budget_reserves_settlement_below_both_limits(
    settings: Any, soft: int, hard: int, seconds: int,
) -> None:
    """Bounded task bodies finish before whichever worker limit comes first."""

    settings.CELERY_TASK_SOFT_TIME_LIMIT = soft
    settings.CELERY_TASK_TIME_LIMIT = hard
    assert task_time_budget() == timedelta(seconds=seconds)


def test_jobs_app_exports_celery_app() -> None:
    """The framework jobs app exposes one configured Celery application."""

    from angee.jobs.celery import app

    assert apps.is_installed("angee.jobs")
    assert app.main == "angee"


@pytest.mark.django_db(transaction=True)
def test_enqueue_task_sends_named_task(monkeypatch: Any) -> None:
    """Callers enqueue by stable task name through the Angee seam."""

    calls: list[tuple[str, dict[str, Any] | None, str | None, float | datetime | None]] = []

    def fake_send_task(
        name: str,
        *,
        kwargs: dict[str, Any] | None = None,
        queue: str | None = None,
        expires: float | datetime | None = None,
    ) -> None:
        calls.append((name, kwargs, queue, expires))

    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", fake_send_task)

    enqueue_task("test.task", kwargs={"run_id": 1}, queue="default")
    enqueue_task(
        "integrate.run_bridge_session",
        kwargs={"model_label": "messaging.channel", "pk": 1},
        queue="whatsapp",
        expires=60.0,
    )

    assert calls == [
        ("test.task", {"run_id": 1}, "default", None),
        (
            "integrate.run_bridge_session",
            {"model_label": "messaging.channel", "pk": 1},
            "whatsapp",
            60.0,
        ),
    ]


@pytest.mark.django_db(transaction=True)
def test_enqueue_task_waits_for_outer_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The jobs owner copies the payload and waits for the enclosing commit."""

    send = Mock()
    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", send)
    payload = {"run_id": 1}
    with transaction.atomic():
        with transaction.atomic():
            enqueue_task("test.task", kwargs=payload)
        payload["run_id"] = 2
        send.assert_not_called()

    send.assert_called_once_with("test.task", kwargs={"run_id": 1}, queue=None, expires=None)


@pytest.mark.django_db(transaction=True)
def test_enqueue_task_discards_rolled_back_savepoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rolling back the producer's savepoint discards its task submission."""

    send = Mock()
    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", send)
    with transaction.atomic():
        with pytest.raises(ValueError, match="rollback"), transaction.atomic():
            enqueue_task("test.task", kwargs={})
            raise ValueError("rollback")
    send.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("atomic", [False, True])
def test_enqueue_task_propagates_send_failure(monkeypatch: pytest.MonkeyPatch, atomic: bool) -> None:
    """Broker failure is observable at an immediate send or the outer commit."""

    send = Mock(side_effect=RuntimeError("broker unavailable"))
    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", send)
    with pytest.raises(RuntimeError, match="broker unavailable"):
        if atomic:
            with transaction.atomic():
                enqueue_task("test.task", kwargs={})
                send.assert_not_called()
        else:
            enqueue_task("test.task", kwargs={})


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("atomic", [False, True])
def test_robust_enqueue_logs_failure_without_suppressing_later_delivery(monkeypatch, caplog, atomic):
    """The queue owner owns robustness as well as the single commit boundary."""
    delivered = []

    def send(name, **kwargs):
        if name == "unavailable":
            raise RuntimeError("Transport temporarily unavailable")
        delivered.append(name)

    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", send)
    if atomic:
        with transaction.atomic():
            enqueue_task("unavailable", kwargs={}, robust=True)
            enqueue_task("later", kwargs={})
            assert delivered == []
    else:
        enqueue_task("unavailable", kwargs={}, robust=True)
        enqueue_task("later", kwargs={})
    assert delivered == ["later"]
    assert "Transport temporarily unavailable" in caplog.text


def test_job_autoconfig_declares_celery_defaults_only() -> None:
    """The framework jobs app owns Celery defaults, not addon task schedules."""

    from angee.jobs.autoconfig import SETTINGS

    assert "CELERY_BEAT_SCHEDULE" not in SETTINGS
    assert "CELERY_BEAT_SCHEDULE:append" not in SETTINGS
    assert SETTINGS["CELERY_TASK_IGNORE_RESULT"] is True
    assert SETTINGS["CELERY_WORKER_PREFETCH_MULTIPLIER"] == 1
    # Beat keeps its schedule in the database through the jobs-owned scheduler.
    assert SETTINGS["CELERY_BEAT_SCHEDULER"] == "angee.jobs.scheduler:DatabaseScheduler"
    assert "CELERY_BEAT_SCHEDULE_FILENAME" not in SETTINGS


@pytest.mark.django_db
def test_beat_database_schedule_is_owned_by_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setup writes declared entries and prunes rows code no longer declares."""

    from celery import Celery
    from django_celery_beat import schedulers as native
    from django_celery_beat.models import IntervalSchedule, PeriodicTask

    from angee.jobs.scheduler import DatabaseScheduler

    # The library recycles connections between reads; the in-memory test
    # database would not survive being closed.
    monkeypatch.setattr(native, "close_old_connections", lambda: None)

    every_minute = IntervalSchedule.objects.create(every=60, period=IntervalSchedule.SECONDS)
    PeriodicTask.objects.create(name="retired.tick", task="retired.tick", interval=every_minute)
    app = Celery("beat-ownership-test", set_as_current=False)
    app.conf.beat_schedule = {"kept.tick": {"task": "kept.tick", "schedule": 30.0}}

    DatabaseScheduler(app=app)

    names = set(PeriodicTask.objects.values_list("name", flat=True))
    assert "retired.tick" not in names
    assert "kept.tick" in names
    # Library defaults (the result backend cleanup) are declared too, never pruned.
    assert "celery.backend_cleanup" in names


def test_beat_keeps_its_last_schedule_when_the_database_read_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedded beat has no supervisor: a failed re-read must not end it."""

    from django.db.utils import OperationalError
    from django_celery_beat.schedulers import DatabaseScheduler as NativeDatabaseScheduler

    from angee.jobs import scheduler as jobs_scheduler
    from angee.jobs.scheduler import DatabaseScheduler

    recycled: list[bool] = []
    monkeypatch.setattr(jobs_scheduler, "close_old_connections", lambda: recycled.append(True))
    scheduler = DatabaseScheduler.__new__(DatabaseScheduler)
    scheduler._schedule = {"kept.tick": object()}

    def unavailable(self: object) -> dict[str, object]:
        raise OperationalError("database restarting")

    monkeypatch.setattr(NativeDatabaseScheduler, "all_as_schedule", unavailable)

    assert scheduler.all_as_schedule() == {"kept.tick": scheduler._schedule["kept.tick"]}
    # The broken connection is dropped so the next re-read reconnects.
    assert recycled == [True]


def test_addons_own_their_periodic_celery_schedules() -> None:
    """Each addon contributes the beat entries for its own task names."""

    from angee.integrate.autoconfig import SETTINGS as INTEGRATE_SETTINGS
    from angee.workflows.autoconfig import SETTINGS as WORKFLOW_SETTINGS

    integrate_schedule = INTEGRATE_SETTINGS["CELERY_BEAT_SCHEDULE:append"]
    workflow_schedule = WORKFLOW_SETTINGS["CELERY_BEAT_SCHEDULE:append"]
    assert isinstance(integrate_schedule, dict)
    assert isinstance(workflow_schedule, dict)

    assert integrate_schedule["integrate.sync_due_bridges"]["task"] == "integrate.sync_due_bridges"
    assert set(workflow_schedule) == {"workflows.tick"}
    assert workflow_schedule["workflows.tick"]["task"] == "workflows.tick"
