"""Workflow execution transactions."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.db import connection, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import StepExecutionMode, StepResult, TransientStepError
from tests.workflows import (
    FixtureStep,
    StepAttempt,
    StepRun,
    WorkflowDispatch,
    advance_once,
    execute_started,
    start_run,
    workflow_with_steps,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("completed_entry", [False, True])
def test_advance_publishes_claim_only_after_commit(
    workflow_engine_tables: None, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch, completed_entry: bool
) -> None:
    """Advance publishes claim only after commit."""
    del no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "entry"}, {"key": "next", "is_entry": False}), edges=(("entry", "next", "done"),)
    )
    run = start_run(workflow)
    if completed_entry:
        advance_once(run)
        execute_started(run)
    with system_context(reason="pulse setup"):
        pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    publications: list[bool] = []
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: publications.append(True))
    with transaction.atomic():
        assert engine.advance_dispatch(pulse.pk, expected_run_id=run.pk) == {"claimed": 1}
        assert publications == []
    assert publications == [True]
    with system_context(reason="advance assertions"):
        pulse.refresh_from_db()
        row = StepRun.objects.select_related("current_attempt").get(
            run_id=run.pk, step__key="next" if completed_entry else "entry"
        )
        dispatch = WorkflowDispatch.objects.get(
            kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=row.current_attempt_id
        )
        if completed_entry:
            assert row.previous.filter(step__key="entry").exists()
    assert pulse.consumed_at is not None
    assert row.status == StepRunStatus.STARTED
    assert row.current_attempt is not None
    assert dispatch.step_attempt_id == row.current_attempt_id


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", [StepExecutionMode.STANDARD, StepExecutionMode.DATABASE_COMMAND])
@pytest.mark.parametrize("result_kind", ["done", "wait", "retry"])
def test_execute_preserves_invocation_transaction_and_schedules_result(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    mode: StepExecutionMode,
    result_kind: str,
) -> None:
    """Execute preserves invocation transaction and schedules result."""
    del no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "entry", "config": {"retry": {"max_attempts": 3, "backoff": {"wait": 7}}}},), edges=()
    )
    run = start_run(workflow)
    row = advance_once(run)[0]
    with system_context(reason="execute setup"):
        attempt = StepAttempt.objects.get(pk=row.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=attempt.pk)
    invocations: list[bool] = []
    wake_at = timezone.now() + timedelta(minutes=1)

    def invoke(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        invocations.append(connection.in_atomic_block)
        if result_kind == "retry":
            raise TransientStepError("Retry this writer operation.")
        if result_kind == "wait":
            return StepResult.wait(until=wake_at)
        return StepResult.done({"count": 1}, outcome="done")

    monkeypatch.setattr(FixtureStep, "run", invoke)
    monkeypatch.setattr(FixtureStep, "execution_mode", mode)
    publications: list[bool] = []
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: publications.append(True))
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {"executed": 1}
    assert invocations == [mode == StepExecutionMode.DATABASE_COMMAND]
    assert publications == [True]
    with system_context(reason="execute assertions"):
        attempt.refresh_from_db()
        dispatch.refresh_from_db()
        row.refresh_from_db()
        assert dispatch.consumed_at is not None
        assert attempt.started_at is not None
        assert attempt.result_recorded_at is not None
        assert attempt.applied_at is not None
        if result_kind == "retry":
            assert StepAttempt.objects.filter(pk=row.current_attempt_id, retry_of_id=attempt.pk).exists()
            assert WorkflowDispatch.objects.filter(
                kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=row.current_attempt_id
            ).exists()
        else:
            assert row.status == (StepRunStatus.WAITING if result_kind == "wait" else StepRunStatus.SUCCEEDED)
            advances = WorkflowDispatch.objects.filter(
                run_id=run.pk, kind=WorkflowDispatchKind.ADVANCE, consumed_at__isnull=True
            )
            assert advances.exists()
            if result_kind == "wait":
                assert advances.filter(available_at=wake_at).exists()
