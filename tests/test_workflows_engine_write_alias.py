"""The selected writer owns the complete ADVANCE and EXECUTE operation."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from datetime import timedelta
from typing import Any

import pytest
from django.db import connections, router, transaction
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.workflows import engine
from angee.workflows.attempts import RecoveryMode
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import StepRunStatus
from angee.workflows.steps import StepExecutionMode, StepResult, TransientStepError
from tests.workflows import (
    FixtureStep,
    StepAttempt,
    StepRun,
    WorkflowDispatch,
    WorkflowRun,
    WorkflowWriteRouter,
    advance_once,
    execute_started,
    start_run,
    workflow_with_steps,
)
from tests.workflows import workflow_audit_frontier as workflow_audit_frontier
from tests.workflows import workflow_authorization_frontier as workflow_authorization_frontier


@pytest.fixture
def engine_writer(
    workflow_engine_tables: None,
    workflow_authorization_frontier: None,
    workflow_audit_frontier: list[dict[str, Any]],
    database_alias: Callable[[str], AbstractContextManager[str]],
) -> Iterator[str]:
    """Expose workflow tables through the shared database alias factory."""

    del workflow_engine_tables
    with database_alias("workflow_engine_writer") as alias:
        yield alias


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("completed_entry", [False, True])
def test_advance_routes_and_claims_after_admission_on_selected_writer(
    engine_writer: str,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    completed_entry: bool,
) -> None:
    """The admitted pulse reads reverse rows, routes joins, claims and commits there."""

    del no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "entry"}, {"key": "next", "is_entry": False}),
        edges=(("entry", "next", "done"),),
    )
    run = start_run(workflow)
    if completed_entry:
        advance_once(run)
        execute_started(run)
    with system_context(reason="writer regression pulse setup"):
        pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    routing = WorkflowWriteRouter(engine_writer)
    monkeypatch.setattr(router, "routers", [routing])
    publications: list[str] = []
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: publications.append(kwargs["using"]))

    with transaction.atomic(using=engine_writer):
        assert engine.advance_dispatch(pulse.pk, expected_run_id=run.pk) == {"claimed": 1}
        assert publications == []

    assert publications == [engine_writer]
    with system_context(reason="writer regression advance assertions"):
        pulse.refresh_from_db(using=engine_writer)
        row = (
            StepRun.objects.using(engine_writer)
            .select_related("current_attempt")
            .get(run_id=run.pk, step__key="next" if completed_entry else "entry")
        )
        dispatch = WorkflowDispatch.objects.using(engine_writer).get(
            kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=row.current_attempt_id
        )
        if completed_entry:
            assert row.previous.using(engine_writer).filter(step__key="entry").exists()
    assert pulse.consumed_at is not None
    assert row.status == StepRunStatus.STARTED
    assert row.current_attempt is not None
    assert row.current_attempt._state.db == engine_writer
    assert dispatch._state.db == engine_writer


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", [StepExecutionMode.STANDARD, StepExecutionMode.DATABASE_COMMAND])
@pytest.mark.parametrize("result_kind", ["done", "wait", "retry"])
def test_execute_reloads_invokes_and_schedules_result_on_selected_writer(
    engine_writer: str,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    mode: StepExecutionMode,
    result_kind: str,
) -> None:
    """Admission is followed by invocation, projection and retained successor work."""

    del no_workflow_queue
    workflow = workflow_with_steps(
        steps=({"key": "entry", "config": {"retry": {"max_attempts": 3, "backoff": {"wait": 7}}}},),
        edges=(),
    )
    run = start_run(workflow)
    row = advance_once(run)[0]
    with system_context(reason="writer regression execute setup"):
        attempt = StepAttempt.objects.get(pk=row.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=attempt.pk)
    attempt_queryset = type(StepAttempt.objects.get_queryset())
    get_attempt = attempt_queryset.get

    def get_with_retained_instances(queryset: Any, *args: Any, **kwargs: Any) -> Any:
        retained = get_attempt(queryset, *args, **kwargs)
        if not isinstance(retained, StepAttempt):
            return retained
        cached_step = retained._state.fields_cache.get("step_run")
        if cached_step is not None:
            cached_run = cached_step._state.fields_cache.get("run")
            if cached_run is not None:
                # An overriding loader may hand EXECUTE previously retained instances.
                cached_step._state.db = "default"
                cached_run._state.db = "default"
        return retained

    monkeypatch.setattr(attempt_queryset, "get", get_with_retained_instances)
    invocations: list[tuple[str, str, bool]] = []
    wake_at = timezone.now() + timedelta(minutes=1)

    def invoke(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        invocations.append(
            (
                get_write_alias(type(step_run), instance=step_run),
                get_write_alias(type(step_run.run), instance=step_run.run),
                connections[engine_writer].in_atomic_block,
            )
        )
        if result_kind == "retry":
            raise TransientStepError("Retry this writer operation.")
        if result_kind == "wait":
            return StepResult.wait(until=wake_at)
        return StepResult.done({"writer": step_run._state.db}, outcome="done")

    monkeypatch.setattr(FixtureStep, "run", invoke)
    monkeypatch.setattr(FixtureStep, "execution_mode", mode)
    monkeypatch.setattr(router, "routers", [WorkflowWriteRouter("default")])
    publications: list[str] = []
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: publications.append(kwargs["using"]))

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token, using=engine_writer) == {"executed": 1}

    assert invocations == [(engine_writer, engine_writer, mode == StepExecutionMode.DATABASE_COMMAND)]
    assert publications == [engine_writer]
    with system_context(reason="writer regression execute assertions"):
        attempt.refresh_from_db(using=engine_writer)
        dispatch.refresh_from_db(using=engine_writer)
        row.refresh_from_db(using=engine_writer)
        assert dispatch.consumed_at is not None
        assert attempt.started_at is not None
        assert attempt.result_recorded_at is not None
        assert attempt.applied_at is not None
        if result_kind == "retry":
            assert (
                StepAttempt.objects.using(engine_writer)
                .filter(pk=row.current_attempt_id, retry_of_id=attempt.pk)
                .exists()
            )
            assert (
                WorkflowDispatch.objects.using(engine_writer)
                .filter(kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=row.current_attempt_id)
                .exists()
            )
        else:
            assert row.status == (StepRunStatus.WAITING if result_kind == "wait" else StepRunStatus.SUCCEEDED)
            advances = WorkflowDispatch.objects.using(engine_writer).filter(
                run_id=run.pk, kind=WorkflowDispatchKind.ADVANCE, consumed_at__isnull=True
            )
            assert advances.exists()
            if result_kind == "wait":
                assert advances.filter(available_at=wake_at).exists()


@pytest.mark.django_db(transaction=True)
def test_recovery_hook_inherits_explicit_writer_without_signature_change(
    engine_writer: str,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recovery override derives the admitted writer from both handed instances."""

    del no_workflow_queue
    workflow = workflow_with_steps(steps=({"key": "entry", "config": {"mode": "error"}},), edges=())
    run = start_run(workflow)
    source_step = advance_once(run)[0]
    execute_started(run)
    with system_context(reason="writer recovery source"):
        source = StepAttempt.objects.get(pk=source_step.current_attempt_id)
    monkeypatch.setattr(FixtureStep, "replay_mode", RecoveryMode.FRESH)
    recovery = WorkflowRun.objects.start_recovery(source, request_key="writer-recovery", actor=run.admission_actor())
    row = advance_once(recovery)[0]
    with system_context(reason="writer recovery execute"):
        attempt = StepAttempt.objects.get(pk=row.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.EXECUTE, step_attempt_id=attempt.pk)
    invocations: list[tuple[str, str, int, RecoveryMode]] = []

    def recover(self: FixtureStep, step_run: Any, *, now: Any, source_attempt: Any, mode: RecoveryMode) -> StepResult:
        del self, now
        invocations.append(
            (
                get_write_alias(type(step_run), instance=step_run),
                get_write_alias(type(step_run.run), instance=step_run.run),
                source_attempt.pk,
                mode,
            )
        )
        return StepResult.done(outcome="done")

    monkeypatch.setattr(FixtureStep, "run_recovery", recover)
    monkeypatch.setattr(router, "routers", [WorkflowWriteRouter("default")])

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token, using=engine_writer) == {"executed": 1}
    assert invocations == [(engine_writer, engine_writer, source.pk, RecoveryMode.FRESH)]
    with system_context(reason="writer recovery result"):
        row.refresh_from_db(using=engine_writer)
    assert row.status == StepRunStatus.SUCCEEDED
