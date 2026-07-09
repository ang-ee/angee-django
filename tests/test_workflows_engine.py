"""Tests for the workflow runtime engine."""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows import steps as workflow_steps
from angee.workflows.steps import HandlerStep, StepResult
from tests.workflows import (
    Decision,
    Edge,
    Step,
    StepRun,
    Trigger,
    Workflow,
    WorkflowRun,
    advance_once,
    execute_started,
    run_to_terminal,
    start_run,
    step_for,
    step_run_for,
    workflow_with_steps,
)

User = get_user_model()
pytest_plugins = ("tests.workflows",)


@pytest.fixture()
def handler_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Make the abstract handler testable by journaling calls from config."""

    calls: list[dict[str, Any]] = []

    def run(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        config = dict(step_run.step.config)
        calls.append(
            {
                "key": step_run.step.key,
                "input": step_run.input,
                "attempt": step_run.attempt,
            }
        )
        if config.get("mode") == "error":
            raise RuntimeError(str(config.get("error", "handler failed")))
        output = {
            "key": step_run.step.key,
            "input": step_run.input,
            **dict(config.get("output", {})),
        }
        return StepResult.done(output=output, outcome=str(config.get("outcome", "done")))

    monkeypatch.setattr(HandlerStep, "run", run)
    return calls


@pytest.mark.django_db(transaction=True)
def test_two_step_run_completes_end_to_end(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A run pins the published version, injects output, and succeeds."""

    del workflow_engine_tables, no_workflow_queue
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "next", "output": {"value": 7}}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )

    run = run_to_terminal(start_run(workflow))

    assert run.status == run_status.SUCCEEDED
    assert run.workflow == workflow
    assert run.steps_taken == 2
    with system_context(reason="test workflows engine result read"):
        rows = list(StepRun.objects.filter(run=run).select_related("step").order_by("step__key"))
    assert [row.status for row in rows] == [step_run_status.SUCCEEDED, step_run_status.SUCCEEDED]
    finish = step_run_for(run, "finish")
    assert finish.input["value"] == 7
    assert [call["key"] for call in handler_calls] == ["start", "finish"]


@pytest.mark.django_db(transaction=True)
def test_crash_replay_does_not_reexecute_completed_steps(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Replaying advance/execute after a completed step reuses journaled output."""

    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "next", "output": {"recorded": "yes"}}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )
    run = start_run(workflow)

    start_row = advance_once(run)[0]
    execute_started(run)
    from angee.workflows import engine

    engine.execute(start_row.pk)
    engine.advance(run.pk)
    engine.advance(run.pk)
    execute_started(run)
    run_to_terminal(run)

    assert [call["key"] for call in handler_calls] == ["start", "finish"]
    assert step_run_for(run, "finish").input["recorded"] == "yes"


@pytest.mark.django_db(transaction=True)
def test_duplicate_advance_claims_a_scheduled_step_once(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Duplicate advance calls are idempotent while a step is already claimed."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)

    advance_once(run)
    advance_once(run)
    run.refresh_from_db()

    assert run.steps_taken == 1
    with system_context(reason="test workflows engine duplicate read"):
        assert StepRun.objects.filter(run=run, status=step_run_status.STARTED).count() == 1
        assert StepRun.objects.filter(run=run, step__key="start").count() == 1


@pytest.mark.django_db(transaction=True)
def test_conditional_branch_skip_cascades_through_all_success_join(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Untaken branch targets skip, and default joins skip behind them."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "left"}},
            {"key": "left", "config": {"outcome": "done"}},
            {"key": "right", "config": {"outcome": "done"}},
            {"key": "join", "config": {"outcome": "done"}},
        ),
        edges=(
            ("entry", "left", "left"),
            ("entry", "right", "right"),
            ("left", "join", ""),
            ("right", "join", ""),
        ),
    )

    run = run_to_terminal(start_run(workflow))

    assert step_run_for(run, "right").status == step_run_status.SKIPPED
    assert step_run_for(run, "join").status == step_run_status.SKIPPED


@pytest.mark.django_db(transaction=True)
def test_none_failed_min_one_success_join_cures_post_branch_skip(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A post-branch join can run when one branch succeeded and the other skipped."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "left"}},
            {"key": "left", "config": {"outcome": "done"}},
            {"key": "right", "config": {"outcome": "done"}},
            {
                "key": "join",
                "join_rule": workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS,
                "config": {"outcome": "done"},
            },
        ),
        edges=(
            ("entry", "left", "left"),
            ("entry", "right", "right"),
            ("left", "join", ""),
            ("right", "join", ""),
        ),
    )

    run = run_to_terminal(start_run(workflow))

    assert run.status == run_status.SUCCEEDED
    assert step_run_for(run, "right").status == step_run_status.SKIPPED
    assert step_run_for(run, "join").status == step_run_status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_one_success_join_runs_without_waiting_for_all_siblings(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The one_success join schedules as soon as one upstream succeeds."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "done"}},
            {"key": "fast", "config": {"outcome": "done"}},
            {"key": "slow", "config": {"outcome": "done"}},
            {
                "key": "join",
                "join_rule": workflow_models.JoinRule.ONE_SUCCESS,
                "config": {"outcome": "done"},
            },
        ),
        edges=(
            ("entry", "fast", ""),
            ("entry", "slow", ""),
            ("fast", "join", ""),
            ("slow", "join", ""),
        ),
    )
    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run, limit=1)

    advance_once(run)

    assert step_run_for(run, "join").status == step_run_status.STARTED
    assert step_run_for(run, "slow").status == step_run_status.STARTED


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("rule", "upstream_statuses", "expected"),
    [
        (workflow_models.JoinRule.ALL_SUCCESS, ("succeeded", "succeeded"), "started"),
        (workflow_models.JoinRule.ALL_SUCCESS, ("succeeded", "skipped"), "skipped"),
        (workflow_models.JoinRule.ONE_DONE, ("failed", "waiting"), "started"),
        (workflow_models.JoinRule.ALL_DONE, ("failed", "canceled"), "started"),
        (workflow_models.JoinRule.NONE_FAILED, ("skipped", "skipped"), "started"),
        (workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS, ("skipped", "skipped"), "absent"),
        (workflow_models.JoinRule.NONE_FAILED_MIN_ONE_SUCCESS, ("skipped", "succeeded"), "started"),
    ],
)
def test_join_rule_truth_table(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    rule: Any,
    upstream_statuses: tuple[str, str],
    expected: str,
) -> None:
    """Join readiness is derived from upstream sibling StepRun statuses."""

    del workflow_engine_tables, no_workflow_queue
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "left", "is_entry": True, "config": {"outcome": "done"}},
            {"key": "right", "is_entry": False, "config": {"outcome": "done"}},
            {"key": "join", "is_entry": False, "join_rule": rule, "config": {"outcome": "done"}},
        ),
        edges=(("left", "join", ""), ("right", "join", "")),
    )
    left = step_for(workflow, "left")
    right = step_for(workflow, "right")
    with system_context(reason="test workflows join table"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        left_row = StepRun.objects.create(
            run=run,
            step=left,
            status=getattr(step_run_status, upstream_statuses[0].upper()),
            output={"left": True},
            outcome="done",
        )
        right_row = StepRun.objects.create(
            run=run,
            step=right,
            status=getattr(step_run_status, upstream_statuses[1].upper()),
            output={"right": True},
            outcome="done",
        )
    advance_once(run)

    with system_context(reason="test workflows engine join read"):
        join_row = StepRun.objects.filter(run=run, step__key="join").first()
    if expected == "absent":
        assert join_row is None
    else:
        assert join_row is not None
        assert join_row.status == getattr(step_run_status, expected.upper())
        if expected == "started":
            with system_context(reason="test workflows engine join previous read"):
                assert set(join_row.previous.all()) == {left_row, right_row}


@pytest.mark.django_db(transaction=True)
def test_content_routing_uses_outcome_edges(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Only edges matching the source outcome are taken."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "classify", "config": {"outcome": "pdf"}},
            {"key": "pdf", "config": {"outcome": "done"}},
            {"key": "image", "config": {"outcome": "done"}},
        ),
        edges=(("classify", "pdf", "pdf"), ("classify", "image", "image")),
    )

    run = run_to_terminal(start_run(workflow))

    assert step_run_for(run, "pdf").status == step_run_status.SUCCEEDED
    assert step_run_for(run, "image").status == step_run_status.SKIPPED


@pytest.mark.django_db(transaction=True)
def test_max_steps_fails_run_before_claiming_next_step(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The engine, not a step impl, enforces the pinned max_steps bound."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        max_steps=1,
        steps=(
            {"key": "start", "config": {"outcome": "next"}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", "next"),),
    )

    run = start_run(workflow)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    run.refresh_from_db()

    assert run.status == run_status.FAILED
    assert "max_steps" in run.error
    assert step_run_for(run, "finish").status == step_run_status.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_timer_wait_resumes_from_wake_sweep(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A durable wait step becomes executable when the sweep sees its wake time."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    now = timezone.now()
    wake_at = now + timedelta(hours=1)
    workflow = workflow_with_steps(
        steps=(
            {"key": "wait", "step_class": "wait", "config": {"until": wake_at.isoformat()}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("wait", "finish", "timer"),),
    )
    run = start_run(workflow)
    advance_once(run, now=now)
    execute_started(run, now=now)
    advance_once(run, now=now)
    run.refresh_from_db()

    wait_row = step_run_for(run, "wait")
    assert wait_row.status == step_run_status.WAITING
    assert run.wake_at == wake_at

    from angee.workflows import engine

    engine.sweep(now=wake_at + timedelta(seconds=1))
    execute_started(run, now=wake_at + timedelta(seconds=1))
    run_to_terminal(run)
    run.refresh_from_db()

    assert step_run_for(run, "wait").status == step_run_status.SUCCEEDED
    assert run.status == run_status.SUCCEEDED


@pytest.mark.django_db(transaction=True)
def test_cancellation_propagates_to_journal_and_child_runs(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Canceling a run cancels durable waits, scheduled rows, and child runs."""

    del workflow_engine_tables, no_workflow_queue
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "entry", "config": {"outcome": "done"}},
            {"key": "queued", "config": {"outcome": "done"}},
            {
                "key": "waiting",
                "step_class": "gate",
                "config": {"action": "approve", "slots": [{"assignee": "auth/user:test-waiting"}]},
            },
        ),
        edges=(("entry", "queued", ""), ("queued", "waiting", "")),
    )
    entry = step_for(workflow, "entry")
    queued = step_for(workflow, "queued")
    waiting = step_for(workflow, "waiting")
    with system_context(reason="test workflows cancel setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        started = StepRun.objects.create(run=run, step=entry, status=step_run_status.STARTED)
        scheduled = StepRun.objects.create(run=run, step=queued, status=step_run_status.SCHEDULED)
        waiting_row = StepRun.objects.create(run=run, step=waiting, status=step_run_status.WAITING)
        child = WorkflowRun.objects.create(
            workflow=workflow,
            parent_step_run=waiting_row,
            status=workflow_models.RunStatus.WAITING,
        )
        StepRun.objects.create(run=child, step=waiting, status=step_run_status.WAITING)

    from angee.workflows import engine

    engine.cancel(run)

    run.refresh_from_db()
    child.refresh_from_db()
    started.refresh_from_db()
    scheduled.refresh_from_db()
    waiting_row.refresh_from_db()
    assert run.status == run_status.CANCELED
    assert child.status == run_status.CANCELED
    assert scheduled.status == step_run_status.CANCELED
    assert waiting_row.status == step_run_status.CANCELED
    assert started.status == step_run_status.STARTED
    assert started.resume_state["cancel_requested"] is True


@pytest.mark.django_db(transaction=True)
def test_transient_step_error_uses_configured_retry_backoff(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient impl failures stay started and Celery retries per step config."""

    del workflow_engine_tables, no_workflow_queue
    transient_error = getattr(workflow_steps, "TransientStepError", None)
    assert transient_error is not None
    step_run_status = workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {
                "key": "start",
                "config": {"retry": {"max_attempts": 3, "backoff": 7}},
            },
        ),
        edges=(),
    )
    run = start_run(workflow)
    step_run = advance_once(run)[0]

    def run_transient(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, step_run, now
        raise transient_error("try again")

    monkeypatch.setattr(HandlerStep, "run", run_transient)

    from angee.workflows import engine, tasks

    with pytest.raises(transient_error):
        engine.execute(step_run.pk)

    step_run.refresh_from_db()
    assert step_run.status == step_run_status.STARTED
    assert step_run.attempt == 1

    policy = tasks._retry_policy_for_step_run(step_run)
    assert policy.max_attempts == 3
    assert tasks._retry_countdown(policy, 1) == 7
    tasks._journal_retry_exhausted(step_run, exception=transient_error("try again"))
    step_run.refresh_from_db()
    assert step_run.status == step_run_status.FAILED
    assert "try again" in step_run.error


@pytest.mark.django_db(transaction=True)
def test_hard_failure_routes_failed_outcome(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Hard impl failures journal outcome=failed and activate matching edges."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"mode": "error", "error": "boom"}},
            {"key": "cleanup", "config": {"outcome": "done"}},
        ),
        edges=(("start", "cleanup", "failed"),),
    )
    run = start_run(workflow)

    advance_once(run)
    execute_started(run)
    advance_once(run)

    failed = step_run_for(run, "start")
    cleanup = step_run_for(run, "cleanup")
    assert failed.status == step_run_status.FAILED
    assert failed.outcome == "failed"
    assert cleanup.status == step_run_status.STARTED

    execute_started(run)
    advance_once(run)
    run.refresh_from_db()
    assert run.status == run_status.FAILED


@pytest.mark.django_db(transaction=True)
def test_step_impl_heartbeat_helper_updates_started_row(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Long-running impls can refresh their own StepRun heartbeat."""

    del workflow_engine_tables, no_workflow_queue
    started_at = timezone.now()
    pulse_at = started_at + timedelta(seconds=30)
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)

    def run_with_heartbeat(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del now
        self.heartbeat(step_run, at=pulse_at)
        return StepResult.done(output={}, outcome="done")

    monkeypatch.setattr(HandlerStep, "run", run_with_heartbeat)
    advance_once(run, now=started_at)
    execute_started(run, now=started_at)

    step_run = step_run_for(run, "start")
    assert step_run.heartbeat_at == pulse_at


@pytest.mark.django_db(transaction=True)
def test_heartbeat_timeout_reaps_started_rows_and_routes_failed_outcome(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    handler_calls: list[dict[str, Any]],
) -> None:
    """The reaper fails stale started rows, enqueues advance, and failed edges route."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 60
    step_run_status = workflow_models.StepRunStatus
    now = timezone.now()
    stale_at = now - timedelta(seconds=61)
    enqueued: list[int] = []
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "done"}},
            {"key": "cleanup", "config": {"outcome": "done"}},
        ),
        edges=(("start", "cleanup", "failed"),),
    )
    run = start_run(workflow)
    advance_once(run, now=stale_at)

    from angee.workflows import engine

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: enqueued.append(run_id))
    assert engine.reap(now=now) == {"reaped": 1}

    failed = step_run_for(run, "start")
    assert failed.status == step_run_status.FAILED
    assert failed.outcome == "failed"
    assert "heartbeat" in failed.error
    assert enqueued == [run.pk]

    advance_once(run, now=now)
    assert step_run_for(run, "cleanup").status == step_run_status.STARTED


@pytest.mark.django_db(transaction=True)
def test_reaper_ignores_waiting_rows(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settings: Any,
    handler_calls: list[dict[str, Any]],
) -> None:
    """Waiting rows represent durable waits and are exempt from heartbeat reaping."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 60
    step_run_status = workflow_models.StepRunStatus
    now = timezone.now()
    workflow = workflow_with_steps(
        steps=(
            {
                "key": "wait",
                "step_class": "wait",
                "config": {"until": (now + timedelta(hours=1)).isoformat()},
            },
        ),
        edges=(),
    )
    run = start_run(workflow)
    advance_once(run, now=now - timedelta(minutes=10))
    execute_started(run, now=now - timedelta(minutes=10))
    waiting = step_run_for(run, "wait")
    with system_context(reason="test workflows waiting stale heartbeat"):
        waiting.heartbeat_at = now - timedelta(minutes=10)
        waiting.save(update_fields=["heartbeat_at", "updated_at"])

    from angee.workflows import engine

    assert engine.reap(now=now) == {"reaped": 0}
    waiting.refresh_from_db()
    assert waiting.status == step_run_status.WAITING


@pytest.mark.django_db(transaction=True)
def test_reaper_finishes_canceled_started_rows(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canceled runs flag started rows; the heartbeat reaper moves those rows terminal."""

    del workflow_engine_tables, no_workflow_queue
    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 60
    run_status, step_run_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    now = timezone.now()
    stale_at = now - timedelta(seconds=61)
    enqueued: list[int] = []
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)
    step_run = advance_once(run, now=stale_at)[0]

    from angee.workflows import engine

    engine.cancel(run)
    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: enqueued.append(run_id))
    assert engine.reap(now=now) == {"reaped": 1}

    run.refresh_from_db()
    step_run.refresh_from_db()
    assert run.status == run_status.CANCELED
    assert step_run.status == step_run_status.FAILED
    assert step_run.resume_state["cancel_requested"] is True
    assert enqueued == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_error_workflow_fires_once_with_failed_run_subject(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A failed run starts its linked error workflow once using the failed StepRun as parent."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    run_status = workflow_models.RunStatus
    error_version = workflow_with_steps(
        name="Error workflow",
        steps=({"key": "recover", "config": {"outcome": "done"}},),
        edges=(),
    )
    with system_context(reason="test workflows error workflow definition"):
        draft = Workflow.objects.create(name="Primary", error_workflow=error_version.published_from)
        Step.objects.create(
            workflow=draft,
            key="explode",
            name="Explode",
            config={"mode": "error", "error": "boom"},
            is_entry=True,
        )
        workflow = draft.publish()
    run = run_to_terminal(start_run(workflow))
    failed = step_run_for(run, "explode")

    from angee.workflows import engine

    engine.advance(run.pk)
    with system_context(reason="test workflows error workflow children"):
        children = list(WorkflowRun.objects.filter(parent_step_run=failed).order_by("pk"))

    assert run.status == run_status.FAILED
    assert len(children) == 1
    child = children[0]
    assert child.workflow == error_version
    assert child.subject == run


@pytest.mark.django_db(transaction=True)
def test_error_workflow_run_does_not_start_another_error_workflow(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    handler_calls: list[dict[str, Any]],
) -> None:
    """A failing run already launched as error handling does not recurse."""

    del workflow_engine_tables, no_workflow_queue, handler_calls
    error_version = workflow_with_steps(
        name="Self error workflow",
        steps=({"key": "recover", "config": {"mode": "error", "error": "recovery failed"}},),
        edges=(),
    )
    with system_context(reason="test workflows cyclic error workflow definition"):
        error_draft = error_version.published_from
        error_draft.error_workflow = error_draft
        error_draft.save(update_fields={"error_workflow", "updated_at"})
        primary = Workflow.objects.create(name="Primary cyclic", error_workflow=error_draft)
        Step.objects.create(
            workflow=primary,
            key="explode",
            name="Explode",
            config={"mode": "error", "error": "boom"},
            is_entry=True,
        )
        workflow = primary.publish()

    run = run_to_terminal(start_run(workflow))
    failed = step_run_for(run, "explode")
    with system_context(reason="test workflows first error workflow child"):
        child = WorkflowRun.objects.get(parent_step_run=failed)

    run_to_terminal(child)

    with system_context(reason="test workflows cyclic error workflow children"):
        assert WorkflowRun.objects.filter(parent_step_run__run=child).count() == 0


@pytest.mark.django_db(transaction=True)
def test_override_run_reuses_existing_terminal_step_run(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Overriding to a failed step reschedules its existing journal row."""

    del workflow_engine_tables, no_workflow_queue
    admin = User.objects.create_user(username="workflow-override-rerun")
    workflow = workflow_with_steps(
        steps=(
            {"key": "active", "config": {"outcome": "done"}},
            {"key": "retry", "config": {"outcome": "done"}, "is_entry": False},
        ),
        edges=(),
    )
    active = step_for(workflow, "active")
    retry = step_for(workflow, "retry")
    with system_context(reason="test workflows override rerun setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=workflow_models.RunStatus.RUNNING)
        StepRun.objects.create(run=run, step=active, status=workflow_models.StepRunStatus.STARTED)
        failed = StepRun.objects.create(
            run=run,
            step=retry,
            status=workflow_models.StepRunStatus.FAILED,
            error="previous failure",
            outcome="failed",
        )

    override = engine.override_run(run, [retry], actor=admin)

    failed.refresh_from_db()
    assert failed.status == workflow_models.StepRunStatus.SCHEDULED
    assert failed.error == ""
    assert failed.outcome == ""
    with system_context(reason="test workflows override rerun previous"):
        assert list(failed.previous.all()) == [override]
        assert StepRun.objects.filter(run=run, step=retry, map_index=-1).count() == 1


@pytest.mark.django_db(transaction=True)
def test_workflow_run_save_uses_loaded_dedup_key_without_extra_select(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Loaded dedup keys are compared from ``from_db`` state, not a save-time SELECT."""

    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    with system_context(reason="test workflows dedup setup"):
        run = WorkflowRun.objects.create(workflow=workflow, dedup_key="dedup:one")
        loaded = WorkflowRun.objects.get(pk=run.pk)

    loaded.error = "updated"
    with CaptureQueriesContext(connection) as queries:
        with system_context(reason="test workflows dedup save"):
            loaded.save(update_fields={"error", "updated_at"})

    sql = "\n".join(query["sql"] for query in queries.captured_queries)
    assert "SELECT" not in sql.upper()


@pytest.mark.django_db(transaction=True)
def test_event_wait_surface_is_not_accepted(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Timer waits remain; event-only waits are an explicit future seam."""

    del workflow_engine_tables, no_workflow_queue
    with pytest.raises(TypeError, match="event"):
        workflow_steps.StepResult.wait(event="message")  # type: ignore[call-arg]
    with pytest.raises(ValidationError, match="until"):
        workflow_steps.WaitStep.validate_config({"event": "message"})


@pytest.mark.skipif(
    os.environ.get("DATABASE_URL", "").split(":", 1)[0] not in {"postgres", "postgresql"},
    reason="requires DATABASE_URL backed by PostgreSQL",
)
@pytest.mark.django_db(transaction=True)
def test_postgres_lock_sql_scopes_joined_engine_queries_to_self(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Joined engine lock queries compile as FOR UPDATE OF the base table only."""

    del workflow_engine_tables, no_workflow_queue
    if connection.vendor != "postgresql":
        pytest.skip("active Django connection is not PostgreSQL")

    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)
    with system_context(reason="test workflows postgres lock setup"):
        step_run = StepRun.objects.get(run=run)
        decision = Decision.objects.create(step_run=step_run, action="approve")

    with transaction.atomic():
        advance_sql = str(WorkflowRun.objects.lock_if_supported().select_related("workflow").filter(pk=run.pk).query)
        decision_sql = str(
            Decision.objects.lock_if_supported()
            .select_related("step_run", "step_run__run", "step_run__step")
            .filter(pk=decision.pk)
            .query
        )

    assert "FOR UPDATE OF" in advance_sql
    assert "FOR UPDATE OF" in decision_sql
    assert "test_workflows_workflow_run" in advance_sql
    assert "test_workflows_decision" in decision_sql
    assert 'test_workflows_workflow"' not in advance_sql.split("FOR UPDATE OF", 1)[1]
    assert 'test_workflows_step_run"' not in decision_sql.split("FOR UPDATE OF", 1)[1]


@pytest.mark.django_db(transaction=True)
def test_publish_retargets_new_starts_without_migrating_trigger(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Existing runs keep v1 while trigger-backed new starts resolve the v2 lineage head."""

    del workflow_engine_tables, no_workflow_queue
    first = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done", "output": {"version": 1}}},),
        edges=(),
    )
    draft = first.published_from
    old_run = start_run(draft)
    with system_context(reason="test workflows publish retarget"):
        trigger = Trigger.objects.create(workflow=draft, enabled=True)
        start = Step.objects.get(workflow=draft, key="start")
        start.config = {"outcome": "done", "output": {"version": 2}}
        start.save()
        second = draft.publish()

    from angee.workflows import engine

    new_run = engine.start(draft, subject=None, actor=None, trigger=trigger)

    old_run.refresh_from_db()
    assert old_run.workflow == first
    assert new_run.workflow == second
    assert trigger.workflow == draft


@pytest.mark.django_db(transaction=True)
def test_published_and_archived_definition_rows_are_immutable(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Step and Edge writes are rejected on published and archived workflow versions."""

    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(
        steps=(
            {"key": "start", "config": {"outcome": "done"}},
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("start", "finish", ""),),
    )
    step = step_for(workflow, "start")
    with system_context(reason="test workflows immutable definitions"):
        edge = Edge.objects.get(workflow=workflow)

        with pytest.raises(ValidationError, match="immutable"):
            Step.objects.create(workflow=workflow, key="late", name="Late")

        step.name = "Edited"
        with pytest.raises(ValidationError, match="immutable"):
            step.save()

        edge.condition = "changed"
        with pytest.raises(ValidationError, match="immutable"):
            edge.save()

        workflow.archive()
        step.name = "Edited archived"
        with pytest.raises(ValidationError, match="immutable"):
            step.save()


@pytest.mark.django_db(transaction=True)
def test_archived_latest_version_refuses_new_runs(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Archiving the latest lineage version prevents falling back to an older published version."""

    del workflow_engine_tables, no_workflow_queue
    first = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    draft = first.published_from
    with system_context(reason="test workflows archive latest"):
        second = draft.publish()
        second.archive()

    with pytest.raises(ValidationError, match="published version|archived"):
        start_run(draft)


@pytest.mark.django_db(transaction=True)
def test_referenced_published_version_delete_is_protected(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """A WorkflowRun's pinned version FK blocks deleting the referenced published version."""

    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "start", "config": {"outcome": "done"}},),
        edges=(),
    )
    start_run(workflow)

    with system_context(reason="test workflows version protect"):
        with pytest.raises(ProtectedError):
            Workflow.objects.filter(pk=workflow.pk).delete()
