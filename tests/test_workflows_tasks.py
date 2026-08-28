"""Tests for workflow Celery task wrappers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from django.utils import timezone

from angee.workflows import tasks as workflow_tasks


@pytest.mark.parametrize(
    ("task", "task_name", "owner_attr"),
    [
        (workflow_tasks.sweep_workflow_runs, "workflows.sweep", "sweep"),
        (workflow_tasks.reap_workflow_step_runs, "workflows.reap", "reap"),
    ],
)
def test_periodic_engine_tasks_accept_timestamp_keyword(
    monkeypatch: pytest.MonkeyPatch,
    task: Any,
    task_name: str,
    owner_attr: str,
) -> None:
    """Periodic workers may pass an injected Unix tick as ``timestamp``."""

    calls: list[bool] = []
    monkeypatch.setattr(workflow_tasks.engine, owner_attr, lambda: calls.append(True))

    task(timestamp=0)

    assert calls == [True]
    assert task.name == task_name


def test_periodic_schedule_trigger_task_accepts_timestamp_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The schedule scan uses the injected ``timestamp`` when provided."""

    calls: list[datetime] = []
    monkeypatch.setattr(
        workflow_tasks.triggers,
        "run_due_schedule_triggers",
        lambda *, now: calls.append(now),
    )

    workflow_tasks.run_workflow_schedule_triggers(timestamp=0)

    assert calls == [datetime.fromtimestamp(0, tz=timezone.get_current_timezone())]
    assert workflow_tasks.run_workflow_schedule_triggers.name == "workflows.schedule_triggers"


def test_periodic_decision_task_accepts_timestamp_keyword(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The decision timer scan uses the injected ``timestamp`` when provided."""

    calls: list[datetime] = []
    monkeypatch.setattr(workflow_tasks.engine, "sweep_decisions", lambda *, now: calls.append(now))

    workflow_tasks.sweep_workflow_decisions(timestamp=0)

    assert calls == [datetime.fromtimestamp(0, tz=timezone.get_current_timezone())]
    assert workflow_tasks.sweep_workflow_decisions.name == "workflows.decisions"


def test_retry_countdown_matches_step_policy() -> None:
    """Celery retry countdown mirrors the static step retry policy."""

    policy = workflow_tasks.StepRetryPolicy(max_attempts=3, wait=7)

    assert workflow_tasks._retry_countdown(policy, 1) == 7
