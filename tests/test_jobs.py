"""Smoke tests for the framework jobs app."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from django.apps import apps


def test_jobs_app_exports_celery_app() -> None:
    """The framework jobs app exposes one configured Celery application."""

    from angee.jobs.celery import app

    assert apps.is_installed("angee.jobs")
    assert app.main == "angee"


def test_enqueue_task_sends_named_task(monkeypatch: Any) -> None:
    """Callers enqueue by stable task name through the Angee seam."""

    calls: list[tuple[str, dict[str, Any] | None, datetime | None, str | None, float | datetime | None]] = []

    def fake_send_task(
        name: str,
        *,
        kwargs: dict[str, Any] | None = None,
        eta: datetime | None = None,
        queue: str | None = None,
        expires: float | datetime | None = None,
    ) -> None:
        calls.append((name, kwargs, eta, queue, expires))

    monkeypatch.setattr("angee.jobs.enqueue.celery_app.send_task", fake_send_task)

    from angee.jobs.enqueue import enqueue_task

    eta = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

    enqueue_task("test.task", kwargs={"run_id": 1}, eta=eta, queue="default")
    enqueue_task(
        "integrate.run_bridge_session",
        kwargs={"model_label": "messaging.channel", "pk": 1},
        queue="whatsapp",
        expires=60.0,
    )

    assert calls == [
        ("test.task", {"run_id": 1}, eta, "default", None),
        (
            "integrate.run_bridge_session",
            {"model_label": "messaging.channel", "pk": 1},
            None,
            "whatsapp",
            60.0,
        ),
    ]


def test_job_autoconfig_declares_celery_defaults_only() -> None:
    """The framework jobs app owns Celery defaults, not addon task schedules."""

    from angee.jobs.autoconfig import SETTINGS

    assert "CELERY_BEAT_SCHEDULE" not in SETTINGS
    assert "CELERY_BEAT_SCHEDULE:append" not in SETTINGS
    assert SETTINGS["CELERY_TASK_IGNORE_RESULT"] is True
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

    assert integrate_schedule["integrate.sync_due_bridges"]["task"] == "integrate.sync_due_bridges"
    assert workflow_schedule["workflows.decisions"]["task"] == "workflows.decisions"
    assert workflow_schedule["workflows.sweep"]["task"] == "workflows.sweep"
    assert workflow_schedule["workflows.reap"]["task"] == "workflows.reap"
    assert workflow_schedule["workflows.schedule_triggers"]["task"] == "workflows.schedule_triggers"
