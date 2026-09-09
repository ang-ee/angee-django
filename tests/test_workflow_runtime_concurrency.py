"""PostgreSQL races across durable workflow runtime owners."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from typing import Any, Callable

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import (
    AttemptResult,
    AttemptResultKind,
    DecisionSpec,
    LeaseRevocationReason,
)
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus, Verdict
from angee.workflows.steps import StepResult
from tests.workflows import Decision, Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL runtime serialization contract"),
]


def _thread(call: Callable[[], Any]) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def _claimed_execution(monkeypatch: pytest.MonkeyPatch, impl: type[Any]) -> tuple[Any, Any, Any, Any]:
    with system_context(reason="runtime race setup"):
        workflow = Workflow.objects.create(name="Runtime race", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: impl)
    advance = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="runtime race execution inspect"):
        step_run.refresh_from_db()
        attempt = step_run.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    return run, step, attempt, dispatch


def test_cancel_fences_result_after_physical_invocation(
    workflow_engine_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoked = Event()
    release = Event()

    class BlockingDone:
        input_model = None

        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del step_run, now
            invoked.set()
            assert release.wait(timeout=5)
            return StepResult.done({"physical": True}, outcome="done")

    run, _step, attempt, dispatch = _claimed_execution(monkeypatch, BlockingDone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(
            _thread,
            lambda: engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token),
        )
        assert invoked.wait(timeout=5)
        engine.cancel(run)
        release.set()
        assert executing.result(timeout=10) == {"executed": 1}

    with system_context(reason="cancel race verification"):
        attempt.refresh_from_db()
        step_run = StepRun.objects.get(pk=attempt.step_run_id)
        dispatch.refresh_from_db()
        pending_advances = WorkflowDispatch.objects.filter(
            run=run,
            kind=WorkflowDispatchKind.ADVANCE,
            consumed_at__isnull=True,
        ).count()
    assert dispatch.consumed_at is not None
    assert attempt.result_kind == str(AttemptResultKind.DONE)
    assert attempt.output_present is True
    assert attempt.output == {"physical": True}
    assert attempt.result_recorded_at is not None
    assert attempt.applied_at is None
    assert attempt.lease_revocation_reason == str(LeaseRevocationReason.CANCELED)
    assert step_run.status == StepRunStatus.CANCELED
    assert pending_advances == 0


def test_override_fences_old_result_and_advances_generation(
    workflow_engine_tables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoked = Event()
    release = Event()

    class BlockingDone:
        input_model = None

        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del step_run, now
            invoked.set()
            assert release.wait(timeout=5)
            return StepResult.done({"old": True}, outcome="done")

    run, step, attempt, dispatch = _claimed_execution(monkeypatch, BlockingDone)
    actor = get_user_model().objects.create_user(username="runtime-race-override")
    with ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(
            _thread,
            lambda: engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token),
        )
        assert invoked.wait(timeout=5)
        engine.override_run(run, [step], actor=actor)
        release.set()
        assert executing.result(timeout=10) == {"executed": 1}

    with system_context(reason="override race verification"):
        attempt.refresh_from_db()
        step_run = StepRun.objects.get(pk=attempt.step_run_id)
        pending_advances = WorkflowDispatch.objects.filter(
            run=run,
            kind=WorkflowDispatchKind.ADVANCE,
            consumed_at__isnull=True,
        ).count()
    assert attempt.applied_at is None
    assert attempt.result_kind == str(AttemptResultKind.DONE)
    assert attempt.output_present is True
    assert attempt.output == {"old": True}
    assert attempt.result_recorded_at is not None
    assert attempt.lease_revoked_at is not None
    assert attempt.lease_revocation_reason == str(LeaseRevocationReason.SUPERSEDED)
    assert step_run.effect_generation == attempt.effect_generation + 1
    assert step_run.status == StepRunStatus.SCHEDULED
    assert pending_advances == 1


def test_reap_records_revocation_without_fabricating_physical_result(
    workflow_engine_tables: None,
) -> None:
    now = timezone.now()
    stale_at = now - timedelta(days=1)
    with system_context(reason="runtime timeout setup"):
        workflow = Workflow.objects.create(name="Runtime timeout")
        step = Step.objects.create(workflow=workflow, key="start", name="Start", is_entry=True)
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=stale_at).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=stale_at)

    assert engine.reap(now=now) == {"reaped": 1}

    with system_context(reason="runtime timeout verification"):
        attempt.refresh_from_db()
        step_run.refresh_from_db()
        pending_advances = WorkflowDispatch.objects.filter(
            run=run,
            kind=WorkflowDispatchKind.ADVANCE,
            consumed_at__isnull=True,
        ).count()
    assert attempt.lease_revocation_reason == str(LeaseRevocationReason.HEARTBEAT_LOST)
    assert attempt.result_recorded_at is None
    assert attempt.result_kind == ""
    assert attempt.output_present is False
    assert attempt.output is None
    assert attempt.applied_at is None
    assert step_run.status == StepRunStatus.FAILED
    assert pending_advances == 1


def test_decision_timer_waits_on_run_before_locking_decision(
    workflow_engine_tables: None,
) -> None:
    now = timezone.now()
    with system_context(reason="decision lock order setup"):
        workflow = Workflow.objects.create(name="Decision lock order")
        step = Step.objects.create(workflow=workflow, key="gate", name="Gate", is_entry=True)
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=now).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=now)
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.SUSPEND,
            decisions=(DecisionSpec(assignees=(), action="approve", expires_at=now),),
            waiting_kind="approval",
        ),
        recorded_at=now,
    )
    with system_context(reason="decision lock order inspect"):
        decision = Decision.objects.get(suspension_attempt=attempt)
    dispatch, created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )
    assert created is True
    timer_reached_run_lock = Event()

    def expire() -> dict[str, int]:
        def observe_run_lock(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
            if "FOR UPDATE" in sql.upper() and WorkflowRun._meta.db_table in sql:
                timer_reached_run_lock.set()
            return execute(sql, params, many, context)

        with connection.execute_wrapper(observe_run_lock):
            return engine.expire_decision_dispatch(dispatch.pk, now=now)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with system_context(reason="decision lock order holder"), transaction.atomic():
            WorkflowRun.objects.select_for_update().get(pk=run.pk)
            future = pool.submit(_thread, expire)
            assert timer_reached_run_lock.wait(timeout=5)
            # A Decision-first implementation would already own this row and NOWAIT here.
            Decision.objects.select_for_update(nowait=True).get(pk=decision.pk)
            # The worker remains blocked until this outer transaction releases the Run.
        assert future.result(timeout=10) == {"resolved": 1}


def test_due_decision_timers_serialize_to_one_policy_projection(
    workflow_engine_tables: None,
) -> None:
    now = timezone.now()
    with system_context(reason="decision timer race setup"):
        workflow = Workflow.objects.create(name="Decision timer race")
        step = Step.objects.create(workflow=workflow, key="gate", name="Gate", is_entry=True)
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=now).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=now)
    result = AttemptResult(
        AttemptResultKind.SUSPEND,
        decisions=(DecisionSpec(assignees=(), action="approve", escalate_at=now, expires_at=now),),
        waiting_kind="approval",
    )
    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=result,
        recorded_at=now,
    )
    with system_context(reason="decision timer race inspect"):
        decision = Decision.objects.get(suspension_attempt=attempt)
    escalate, escalate_created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_ESCALATE, decision
    )
    expire, expire_created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )
    assert escalate_created is True
    assert expire_created is True
    assert len(finalized.timer_intents) == 2
    starting = Barrier(2)

    def escalate_due() -> dict[str, int]:
        starting.wait(timeout=5)
        return engine.escalate_decision_dispatch(escalate.pk, now=now)

    def expire_due() -> dict[str, int]:
        starting.wait(timeout=5)
        return engine.expire_decision_dispatch(expire.pk, now=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = (
            pool.submit(_thread, escalate_due),
            pool.submit(_thread, expire_due),
        )
        outcomes = [future.result(timeout=10) for future in futures]

    assert sum(result["resolved"] for result in outcomes) == 1
    with system_context(reason="decision timer race verification"):
        decision.refresh_from_db()
        step_run.refresh_from_db()
        escalate.refresh_from_db()
        expire.refresh_from_db()
    assert decision.verdict in {Verdict.ESCALATED, Verdict.EXPIRED}
    assert step_run.status == StepRunStatus.SUCCEEDED
    assert escalate.consumed_at is not None
    assert expire.consumed_at is not None
