"""Tests for workflow Procrastinate task wrappers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from django.utils import timezone
from procrastinate.contrib.django import app as procrastinate_app

from angee.workflows import tasks as workflow_tasks


@pytest.mark.parametrize(
    ("task", "task_name", "owner_attr"),
    [
        (workflow_tasks.sweep_workflow_runs, "workflows.sweep", "sweep"),
        (workflow_tasks.reap_workflow_step_runs, "workflows.reap", "reap"),
    ],
)
def test_periodic_engine_tasks_accept_procrastinate_timestamp_keyword(
    monkeypatch: pytest.MonkeyPatch,
    task: Any,
    task_name: str,
    owner_attr: str,
) -> None:
    """Periodic workers pass the injected Unix tick as ``timestamp``."""

    calls: list[bool] = []
    monkeypatch.setattr(workflow_tasks.engine, owner_attr, lambda: calls.append(True))

    task(timestamp=0)

    assert calls == [True]
    assert task_name in procrastinate_app.tasks


def test_periodic_schedule_trigger_task_accepts_procrastinate_timestamp_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schedule scan uses Procrastinate's persisted ``timestamp`` kwarg."""

    calls: list[datetime] = []
    monkeypatch.setattr(
        workflow_tasks.triggers,
        "run_due_schedule_triggers",
        lambda *, now: calls.append(now),
    )

    workflow_tasks.run_workflow_schedule_triggers(timestamp=0)

    assert calls == [datetime.fromtimestamp(0, tz=timezone.get_current_timezone())]
    assert "workflows.schedule_triggers" in procrastinate_app.tasks
