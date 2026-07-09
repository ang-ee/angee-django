"""Smoke tests for the framework task seam."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from django.apps import apps


def test_tasks_app_exports_celery_app() -> None:
    """The framework task seam exposes one configured Celery application."""

    from angee.tasks.celery import app

    assert apps.is_installed("angee.tasks")
    assert app.main == "angee"
    assert app.conf.task_ignore_result is True


def test_enqueue_task_sends_named_task(monkeypatch: Any) -> None:
    """Callers enqueue by stable task name through the Angee seam."""

    calls: list[tuple[str, dict[str, Any] | None, datetime | None, str | None]] = []

    def fake_send_task(
        name: str,
        *,
        kwargs: dict[str, Any] | None = None,
        eta: datetime | None = None,
        queue: str | None = None,
    ) -> None:
        calls.append((name, kwargs, eta, queue))

    monkeypatch.setattr("angee.tasks.enqueue.celery_app.send_task", fake_send_task)

    from angee.tasks.enqueue import enqueue_task

    eta = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

    enqueue_task("workflows.advance", kwargs={"run_id": 1}, eta=eta, queue="default")

    assert calls == [("workflows.advance", {"run_id": 1}, eta, "default")]


def test_task_autoconfig_declares_periodic_celery_schedule() -> None:
    """Celery beat owns the framework's static periodic ticks."""

    from angee.tasks.autoconfig import SETTINGS

    schedule = SETTINGS["CELERY_BEAT_SCHEDULE"]

    assert schedule["integrate.sync_due_bridges"]["task"] == "integrate.sync_due_bridges"
    assert schedule["workflows.sweep"]["task"] == "workflows.sweep"
    assert schedule["workflows.reap"]["task"] == "workflows.reap"
    assert schedule["workflows.schedule_triggers"]["task"] == "workflows.schedule_triggers"
