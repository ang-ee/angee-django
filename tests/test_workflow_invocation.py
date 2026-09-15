"""Physical workflow invocation authority and fenced local commit tests."""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import replace
from threading import Event
from typing import Any, Callable

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import (
    AttemptResult,
    AttemptResultKind,
    RecoveryCapability,
    RecoveryMode,
)
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.manager_authority import _physical_invocation_for
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepImpl, StepInvocationFenced, StepResult
from tests.workflows import Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun

pytestmark = pytest.mark.django_db(transaction=True)


def _user_count(**lookups: Any) -> int:
    with system_context(reason="workflow invocation user evidence"):
        return get_user_model().objects.filter(**lookups).count()


def _thread(call: Callable[[], Any]) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout TO '5s'")
        return call()
    finally:
        connections.close_all()


def _claimed_execution(
    monkeypatch: pytest.MonkeyPatch,
    implementation: type[StepImpl],
) -> tuple[Any, Any, Any, Any]:
    with system_context(reason="workflow invocation setup"):
        workflow = Workflow.objects.create(name="Physical invocation", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(
            run=run,
            step=step,
            status=StepRunStatus.SCHEDULED,
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: implementation)
    advance = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="workflow invocation inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    return run, step_run, attempt, dispatch


def test_direct_and_post_return_retained_calls_are_fenced(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    observed: dict[str, Any] = {}

    class CommitStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            observed["implementation"] = self
            observed["step_run"] = step_run
            observed["context"] = copy_context()
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="inside-invocation"),
            )
            return StepResult.done({}, outcome="done")

    _run, step_run, attempt, dispatch = _claimed_execution(monkeypatch, CommitStep)
    direct = CommitStep()
    with pytest.raises(StepInvocationFenced, match="active workflow invocation"):
        direct.commit_current(
            step_run,
            lambda: get_user_model().objects.create(username="direct-invocation"),
        )
    with pytest.raises(StepInvocationFenced, match="active physical invocation"):
        direct.heartbeat(step_run)

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {
        "executed": 1
    }
    assert _user_count(username="inside-invocation") == 1
    assert _user_count(username="direct-invocation") == 0

    with pytest.raises(StepInvocationFenced, match="active workflow invocation"):
        observed["implementation"].commit_current(
            observed["step_run"],
            lambda: get_user_model().objects.create(username="after-invocation"),
        )
    with pytest.raises(StepInvocationFenced, match="no longer active"):
        observed["context"].run(
            observed["implementation"].commit_current,
            observed["step_run"],
            lambda: get_user_model().objects.create(username="copied-context"),
        )
    assert _user_count(username="after-invocation") == 0
    assert _user_count(username="copied-context") == 0


def test_live_invocation_allows_multiple_commits_and_observable_heartbeat(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    pulse_at = timezone.now()

    class MultiCommitStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="bounded-commit-1"),
            )
            self.heartbeat(step_run, at=pulse_at)
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="bounded-commit-2"),
            )
            return StepResult.done({}, outcome="done")

    _run, step_run, attempt, dispatch = _claimed_execution(monkeypatch, MultiCommitStep)
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {
        "executed": 1
    }

    with system_context(reason="workflow invocation heartbeat verify"):
        attempt.refresh_from_db()
        step_run.refresh_from_db()
    assert attempt.heartbeat_at >= pulse_at
    assert step_run.heartbeat_at >= pulse_at
    assert _user_count(username__startswith="bounded-commit-") == 2


def test_cross_step_and_reconstructed_capabilities_are_fenced(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    state: dict[str, Any] = {}

    class IdentityStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            alias = step_run._state.db
            invocation = _physical_invocation_for(
                alias=alias,
                step_run_id=step_run.pk,
            )
            assert invocation is not None
            state["invocation"] = invocation
            with pytest.raises(StepInvocationFenced, match="this step run"):
                self.commit_current(state["other_step_run"], lambda: None)
            with ThreadPoolExecutor(max_workers=1) as pool:
                cross_thread = pool.submit(self.commit_current, step_run, lambda: None)
                with pytest.raises(StepInvocationFenced, match="active workflow invocation"):
                    cross_thread.result(timeout=5)
            for forged in (
                replace(invocation, lease_token=uuid.uuid4()),
                replace(invocation),
                replace(invocation, run_id=invocation.run_id + 1),
                replace(invocation, alias="other"),
            ):
                with pytest.raises(StepInvocationFenced, match="no longer active"):
                    StepAttempt.objects.commit_current(forged, writer=lambda: None)
            return StepResult.done({}, outcome="done")

    run, _step_run, attempt, dispatch = _claimed_execution(monkeypatch, IdentityStep)
    with system_context(reason="cross step setup"):
        other_step = Step.objects.create(
            workflow=run.workflow,
            key="other",
            name="Other",
            step_class="agent_session",
        )
        state["other_step_run"] = StepRun.objects.create(
            run=run,
            step=other_step,
            status=StepRunStatus.SCHEDULED,
        )

    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {
        "executed": 1
    }
    with pytest.raises(StepInvocationFenced, match="no longer active"):
        StepAttempt.objects.commit_current(state["invocation"], writer=lambda: None)


def test_writer_exception_rolls_back_and_invocation_scope_closes(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    observed: dict[str, Any] = {}

    class FailingWriterStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            observed["implementation"] = self
            observed["step_run"] = step_run

            def writer() -> None:
                get_user_model().objects.create(username="rolled-back-writer")
                raise RuntimeError("writer failed")

            self.commit_current(step_run, writer)
            raise AssertionError("unreachable")

    _run, _step_run, attempt, dispatch = _claimed_execution(monkeypatch, FailingWriterStep)
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {
        "executed": 1
    }
    with system_context(reason="failed invocation inspect"):
        attempt.refresh_from_db()
    assert attempt.result_kind == str(AttemptResultKind.ERROR)
    assert _user_count(username="rolled-back-writer") == 0
    with pytest.raises(StepInvocationFenced, match="active workflow invocation"):
        observed["implementation"].commit_current(observed["step_run"], lambda: None)


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL invocation/cancellation lock contract",
)
def test_cancel_before_commit_fences_the_writer(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    invoked = Event()
    release = Event()

    class BlockedCommitStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            invoked.set()
            assert release.wait(timeout=10)
            self.heartbeat(step_run)
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="fenced-after-cancel"),
            )
            return StepResult.done({}, outcome="done")

    run, _step_run, attempt, dispatch = _claimed_execution(monkeypatch, BlockedCommitStep)
    with ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(
            _thread,
            lambda: engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token),
        )
        assert invoked.wait(timeout=10)
        engine.cancel(run)
        release.set()
        assert executing.result(timeout=15) == {"executed": 1}

    assert _user_count(username="fenced-after-cancel") == 0
    with system_context(reason="cancel before commit inspect"):
        attempt.refresh_from_db()
    assert attempt.result_kind == str(AttemptResultKind.ERROR)
    assert attempt.applied_at is None


@pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="PostgreSQL invocation/cancellation lock contract",
)
def test_commit_before_cancel_preserves_one_authorized_write(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    committed = Event()
    release = Event()

    class CommittedThenBlockedStep(StepImpl):
        def run(self, step_run: Any, *, now: Any) -> StepResult:
            del now
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="committed-before-cancel"),
            )
            committed.set()
            assert release.wait(timeout=10)
            return StepResult.done({}, outcome="done")

    run, _step_run, attempt, dispatch = _claimed_execution(
        monkeypatch,
        CommittedThenBlockedStep,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        executing = pool.submit(
            _thread,
            lambda: engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token),
        )
        assert committed.wait(timeout=10)
        engine.cancel(run)
        release.set()
        assert executing.result(timeout=15) == {"executed": 1}

    assert _user_count(username="committed-before-cancel") == 1
    with system_context(reason="commit before cancel inspect"):
        attempt.refresh_from_db()
    assert attempt.result_kind == str(AttemptResultKind.DONE)
    assert attempt.applied_at is None


def test_recovery_execution_uses_the_same_invocation_fence(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="invocation-recovery-owner")

    class RecoveryCommitStep(StepImpl):
        @classmethod
        def recovery_capability(cls, *, attempt: Any) -> RecoveryCapability:
            del attempt
            return RecoveryCapability(RecoveryMode.RECONCILE)

        def run_recovery(
            self,
            step_run: Any,
            *,
            now: Any,
            source_attempt: Any,
            mode: RecoveryMode,
        ) -> StepResult:
            del now, source_attempt
            assert mode is RecoveryMode.RECONCILE
            self.commit_current(
                step_run,
                lambda: get_user_model().objects.create(username="recovery-fenced-commit"),
            )
            return StepResult.done({}, outcome="done")

    with system_context(reason="invocation recovery setup"):
        workflow = Workflow.objects.create(
            name="Invocation recovery",
            created_by=actor,
            updated_by=actor,
        )
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        source_run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            created_by=actor,
        )
        source_step_run = StepRun.objects.create(
            run=source_run,
            step=step,
            status=StepRunStatus.SCHEDULED,
        )
    source = StepAttempt.objects.claim(source_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        source.pk,
        lease_token=source.lease_token,
        at=timezone.now(),
    )
    StepAttempt.objects.finalize(
        source.pk,
        lease_token=source.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="recover me"),
        recorded_at=timezone.now(),
    )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: RecoveryCommitStep)

    recovery = WorkflowRun.objects.start_recovery(
        source,
        request_key="invocation-recovery",
        actor=actor,
    )
    with system_context(reason="invocation recovery dispatch"):
        advance = WorkflowDispatch.objects.get(
            run=recovery,
            kind=WorkflowDispatchKind.ADVANCE,
        )
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="invocation recovery attempt"):
        recovery_step = StepRun.objects.get(run=recovery, step=step)
        attempt = StepAttempt.objects.get(pk=recovery_step.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token) == {
        "executed": 1
    }
    assert _user_count(username="recovery-fenced-commit") == 1
