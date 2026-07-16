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

    monkeypatch.setattr("angee.tasks.enqueue.celery_app.send_task", fake_send_task)

    from angee.tasks.enqueue import enqueue_task

    eta = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)

    enqueue_task("workflows.advance", kwargs={"run_id": 1}, eta=eta, queue="default")
    enqueue_task(
        "integrate.run_bridge_session",
        kwargs={"model_label": "messaging.channel", "pk": 1},
        queue="whatsapp",
        expires=60.0,
    )

    assert calls == [
        ("workflows.advance", {"run_id": 1}, eta, "default", None),
        (
            "integrate.run_bridge_session",
            {"model_label": "messaging.channel", "pk": 1},
            None,
            "whatsapp",
            60.0,
        ),
    ]


def test_task_autoconfig_declares_celery_defaults_only() -> None:
    """The framework task app owns Celery defaults, not addon task schedules."""

    from angee.tasks.autoconfig import SETTINGS

    assert "CELERY_BEAT_SCHEDULE" not in SETTINGS
    assert "CELERY_BEAT_SCHEDULE:append" not in SETTINGS
    assert SETTINGS["CELERY_TASK_IGNORE_RESULT"] is True


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
