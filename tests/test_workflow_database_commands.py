"""Database command effects and attempt finalization share one fenced transaction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from threading import Barrier, Event
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context, to_subject_ref

from angee.testing.models import Step, StepArtifact, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun
from angee.workflows import engine
from angee.workflows.attempts import ArtifactSpec, InvocationAdmission, LeaseRevocationReason
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepEffect, StepExecutionMode, StepResult
from angee.workflows_extraction.steps import ProcessEvidenceStepImpl

User = get_user_model()


class _DatabaseCommandImpl:
    """A business-row write whose runtime contract is separate from authoring metadata."""

    input_model = None
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    entered: Event | None = None
    release: Event | None = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del now
        if self.entered is not None:
            self.entered.set()
        if self.release is not None and not self.release.wait(timeout=5):
            raise RuntimeError("Command test release timed out.")
        with system_context(reason="fixture database command write"):
            User.objects.filter(pk=step_run.input["user_id"]).update(first_name="committed")
            changed = User.objects.get(pk=step_run.input["user_id"])
        return StepResult.done(
            {"user_id": changed.pk},
            outcome="applied",
            artifacts=(ArtifactSpec(changed, "Committed user"),),
        )


class _ProcessEvidenceCommandProbe(ProcessEvidenceStepImpl):
    """Exercise the extraction operation's declared boundary with a visible write."""

    entered = None
    release = None
    run = _DatabaseCommandImpl.run


class _RunCancelDatabaseCommand:
    """Retain one exact external wait until another WorkflowRun is terminal."""

    input_model = None
    effect = StepEffect.WRITE
    execution_mode = StepExecutionMode.DATABASE_COMMAND
    actor: Any = None
    target_run_id: int | None = None
    rendezvous: Barrier | None = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        if self.actor is None or self.target_run_id is None:
            raise RuntimeError("Run-cancel fixture is not configured.")
        with system_context(reason="run cancellation command target"):
            target = WorkflowRun.objects.get(pk=self.target_run_id)
        if target.status in RunStatus.TERMINAL:
            return StepResult.done(
                {"target_run_id": target.pk},
                outcome="retired",
                artifacts=(ArtifactSpec(target, "Retired Workflow run"),),
            )
        if self.rendezvous is not None:
            self.rendezvous.wait(timeout=5)
        engine.schedule_run_cancel(step_run, target, actor=self.actor)
        return StepResult.wait(
            until=now + timedelta(minutes=1),
            waiting_kind="external",
            artifacts=(ArtifactSpec(target, "Workflow run awaiting retirement"),),
        )


def _retained_facts(attempt: Any, run: Any) -> tuple[int, int]:
    """Count the command's artifact and continuation rows under engine authority."""

    with system_context(reason="inspect database command retained facts"):
        artifacts = StepArtifact.objects.filter(attempt=attempt).count()
        advances = WorkflowDispatch.objects.filter(
            run=run,
            kind=WorkflowDispatchKind.ADVANCE,
            consumed_at__isnull=True,
        ).count()
    return artifacts, advances


def _scheduled_command(
    monkeypatch: pytest.MonkeyPatch,
    *,
    implementation: type = _DatabaseCommandImpl,
) -> tuple[Any, Any, Any, Any]:
    user = User.objects.create_user(username=f"wf-command-{timezone.now().timestamp()}", first_name="original")
    with system_context(reason="database command test setup"):
        workflow = Workflow.objects.create(name="Database command", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="apply",
            name="Apply",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(
            run=run,
            step=step,
            status=StepRunStatus.SCHEDULED,
            input={"user_id": user.pk},
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: implementation)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(pulse.pk)["claimed"] == 1
    with system_context(reason="database command test inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    return user, step_run, attempt, dispatch


def _scheduled_run_cancel_command(
    monkeypatch: pytest.MonkeyPatch,
    *,
    actor: Any,
    target: Any,
    suffix: str,
) -> tuple[Any, Any, Any]:
    with system_context(reason="run cancellation command setup"):
        workflow = Workflow.objects.create(
            name=f"Run cancellation {suffix}",
            max_steps=10,
        )
        step = Step.objects.create(
            workflow=workflow,
            key="retire",
            name="Retire",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            admitted_actor_ref=str(to_subject_ref(actor)),
        )
        step_run = StepRun.objects.create(
            run=run,
            step=step,
            status=StepRunStatus.SCHEDULED,
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _RunCancelDatabaseCommand)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(pulse.pk)["claimed"] == 1
    with system_context(reason="run cancellation command inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    _RunCancelDatabaseCommand.actor = actor
    _RunCancelDatabaseCommand.target_run_id = target.pk
    return step_run, attempt, dispatch


@pytest.mark.django_db(transaction=True)
def test_revoked_after_invocation_admission_is_fenced_before_domain_write(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_command(monkeypatch)
    before = _retained_facts(attempt, step_run.run)
    manager_type = type(StepAttempt.objects)
    original_admit = manager_type.admit_invocation

    def revoke_after_admit(manager: Any, *args: Any, **kwargs: Any) -> InvocationAdmission:
        admission = original_admit(manager, *args, **kwargs)
        if admission == InvocationAdmission.FIRST_START:
            revoked = manager.revoke(
                attempt.pk,
                lease_token=attempt.lease_token,
                reason=LeaseRevocationReason.HEARTBEAT_LOST,
                at=timezone.now(),
            )
            assert revoked.revoked
        return admission

    monkeypatch.setattr(manager_type, "admit_invocation", revoke_after_admit)
    engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)

    user.refresh_from_db()
    with system_context(reason="verify stale database command"):
        attempt.refresh_from_db()
    assert user.first_name == "original"
    assert attempt.applied_at is None
    assert _retained_facts(attempt, step_run.run) == before


@pytest.mark.django_db(transaction=True)
def test_finalization_failure_rolls_back_domain_command_and_result(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_command(monkeypatch)
    before = _retained_facts(attempt, step_run.run)

    def fail_finalize(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected finalization failure")

    monkeypatch.setattr(type(StepAttempt.objects), "finalize", fail_finalize)
    with pytest.raises(RuntimeError, match="injected finalization failure"):
        engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)

    user.refresh_from_db()
    with system_context(reason="verify database command rollback"):
        attempt.refresh_from_db()
    assert user.first_name == "original"
    assert attempt.result_recorded_at is None
    assert _retained_facts(attempt, step_run.run) == before


@pytest.mark.django_db(transaction=True)
def test_process_evidence_has_no_implicit_replay_and_rolls_back_with_finalization(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    capability = ProcessEvidenceStepImpl.recovery_capability(attempt=object())
    assert capability.mode is None

    user, step_run, attempt, dispatch = _scheduled_command(
        monkeypatch,
        implementation=_ProcessEvidenceCommandProbe,
    )
    before = _retained_facts(attempt, step_run.run)

    def fail_finalize(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("injected process-evidence finalization failure")

    monkeypatch.setattr(type(StepAttempt.objects), "finalize", fail_finalize)
    with pytest.raises(RuntimeError, match="injected process-evidence finalization failure"):
        engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)

    user.refresh_from_db()
    with system_context(reason="verify process-evidence command rollback"):
        attempt.refresh_from_db()
    assert user.first_name == "original"
    assert attempt.result_recorded_at is None
    assert _retained_facts(attempt, step_run.run) == before


@pytest.mark.django_db(transaction=True)
def test_continuation_failure_rolls_back_command_result_and_artifact_before_failure_audit(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_command(monkeypatch)
    before_artifacts, before_advances = _retained_facts(attempt, step_run.run)
    manager_type = type(WorkflowDispatch.objects)
    original_schedule = manager_type.schedule_advance
    failed = False

    def fail_once(manager: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected continuation failure")
        return original_schedule(manager, *args, **kwargs)

    monkeypatch.setattr(manager_type, "schedule_advance", fail_once)
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 1

    user.refresh_from_db()
    with system_context(reason="verify failed command audit after rollback"):
        attempt.refresh_from_db()
    assert user.first_name == "original"
    assert attempt.result_kind == "error"
    assert attempt.error == "injected continuation failure"
    assert _retained_facts(attempt, step_run.run) == (
        before_artifacts,
        before_advances + 1,
    )


@pytest.mark.django_db(transaction=True)
def test_committed_success_replay_does_not_repeat_domain_command(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_command(monkeypatch)
    before_artifacts, before_advances = _retained_facts(attempt, step_run.run)
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 1
    with system_context(reason="verify committed database command"):
        attempt.refresh_from_db()
    assert attempt.applied_at is not None
    assert attempt.output == {"user_id": user.pk}
    assert _retained_facts(attempt, step_run.run) == (
        before_artifacts + 1,
        before_advances + 1,
    )

    with system_context(reason="change fixture after committed result"):
        User.objects.filter(pk=user.pk).update(first_name="after-first-delivery")
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 0
    user.refresh_from_db()
    assert user.first_name == "after-first-delivery"
    assert _retained_facts(attempt, step_run.run) == (
        before_artifacts + 1,
        before_advances + 1,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("parent_relation", (None, "owned_call", "continuation"))
def test_run_cancel_waits_for_committed_cancellation_before_continuing(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    parent_relation: str | None,
) -> None:
    del composed_tables, no_workflow_queue
    actor = User.objects.create_user(username="run-cancel-owner")
    with system_context(reason="run cancellation target setup"):
        target_workflow = Workflow.objects.create(name="Retired target")
        parent_step_run = None
        if parent_relation:
            parent = WorkflowRun.objects.create(
                workflow=target_workflow,
                status=RunStatus.RUNNING,
                created_by=actor,
            )
            parent_step = Step.objects.create(
                workflow=target_workflow,
                key=f"parent-{parent_relation}",
                name="Parent",
                step_class="fixture",
            )
            parent_step_run = StepRun.objects.create(run=parent, step=parent_step)
        target = WorkflowRun.objects.create(
            workflow=target_workflow,
            status=RunStatus.RUNNING,
            created_by=actor,
            parent_step_run=parent_step_run,
            parent_relation=parent_relation,
        )
    step_run, attempt, execute = _scheduled_run_cancel_command(
        monkeypatch,
        actor=actor,
        target=target,
        suffix="ordered",
    )

    with pytest.raises(ValidationError, match="current invocation lease"):
        WorkflowDispatch.objects.schedule_run_cancel(
            step_run.pk,
            target,
            actor=actor,
            lease_token=attempt.lease_token,
        )

    assert engine.execute_dispatch(
        execute.pk,
        attempt.pk,
        attempt.lease_token,
    ) == {"executed": 1}
    with system_context(reason="run cancellation wait verification"):
        step_run.refresh_from_db()
        target.refresh_from_db()
        intent = WorkflowDispatch.objects.get(
            kind=WorkflowDispatchKind.RUN_CANCEL,
            run=target,
        )
    assert step_run.status == StepRunStatus.WAITING
    assert target.status == RunStatus.RUNNING

    with pytest.raises(ValidationError, match="Transport envelope"):
        engine.cancel_run_dispatch(intent.pk, expected_run_id=target.pk + 1)
    assert engine.cancel_run_dispatch(intent.pk, expected_run_id=target.pk) == {"canceled": 1}
    assert engine.cancel_run_dispatch(intent.pk, expected_run_id=target.pk) == {"canceled": 0}
    with system_context(reason="run cancellation delivery verification"):
        target.refresh_from_db()
        intent.refresh_from_db()
        delivery = WorkflowDispatch.objects.get(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_object_id=target.pk,
            consumed_at__isnull=True,
        )
    assert target.status == RunStatus.CANCELED
    assert intent.consumed_at is not None
    assert engine.deliver_artifact_dispatch(delivery.pk)["woken"] == 1

    with system_context(reason="run cancellation continuation"):
        continuation = (
            WorkflowDispatch.objects.filter(
                kind=WorkflowDispatchKind.ADVANCE,
                run=step_run.run,
                available_at__lte=timezone.now(),
                consumed_at__isnull=True,
            )
            .order_by("pk")
            .first()
        )
    assert continuation is not None
    assert engine.advance_dispatch(continuation.pk)["claimed"] == 1
    with system_context(reason="run cancellation continuation execution"):
        step_run.refresh_from_db()
        successor = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        successor_dispatch = WorkflowDispatch.objects.get(step_attempt=successor)
    assert engine.execute_dispatch(
        successor_dispatch.pk,
        successor.pk,
        successor.lease_token,
    ) == {"executed": 1}
    with system_context(reason="run cancellation completion verification"):
        step_run.refresh_from_db()
        successor.refresh_from_db()
    assert step_run.status == StepRunStatus.SUCCEEDED
    assert successor.output == {"target_run_id": target.pk}


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL unique-intent race")
def test_concurrent_database_commands_share_one_run_cancel_intent(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    actor = User.objects.create_user(username="run-cancel-race-owner")
    with system_context(reason="run cancellation race target setup"):
        target_workflow = Workflow.objects.create(name="Concurrent retired target")
        target = WorkflowRun.objects.create(
            workflow=target_workflow,
            status=RunStatus.RUNNING,
            created_by=actor,
        )
    first = _scheduled_run_cancel_command(
        monkeypatch,
        actor=actor,
        target=target,
        suffix="race-a",
    )
    second = _scheduled_run_cancel_command(
        monkeypatch,
        actor=actor,
        target=target,
        suffix="race-b",
    )
    _RunCancelDatabaseCommand.rendezvous = Barrier(2)

    def execute_retirement(values: tuple[Any, Any, Any]) -> dict[str, int]:
        _step_run, current, dispatch = values
        close_old_connections()
        try:
            with system_context(reason="concurrent run cancellation command"):
                return engine.execute_dispatch(
                    dispatch.pk,
                    current.pk,
                    current.lease_token,
                )
        finally:
            connections.close_all()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = tuple(
                future.result(timeout=10)
                for future in (
                    pool.submit(execute_retirement, first),
                    pool.submit(execute_retirement, second),
                )
            )
        assert outcomes == ({"executed": 1}, {"executed": 1})
        with system_context(reason="concurrent run cancellation verification"):
            assert (
                WorkflowDispatch.objects.filter(
                    kind=WorkflowDispatchKind.RUN_CANCEL,
                    run=target,
                    consumed_at__isnull=True,
                ).count()
                == 1
            )
            assert all(
                StepRun.objects.get(pk=values[0].pk).status == StepRunStatus.WAITING for values in (first, second)
            )
    finally:
        _RunCancelDatabaseCommand.rendezvous = None


@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL row-lock contract")
def test_revoke_waits_for_fenced_command_and_observes_committed_result(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    user, step_run, attempt, dispatch = _scheduled_command(monkeypatch)
    before_artifacts, before_advances = _retained_facts(attempt, step_run.run)
    entered, release = Event(), Event()
    _DatabaseCommandImpl.entered = entered
    _DatabaseCommandImpl.release = release

    def in_thread(call: Any) -> Any:
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout TO '5s'")
            with system_context(reason="database command revoke race"):
                return call()
        finally:
            connections.close_all()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            executing = pool.submit(
                in_thread,
                lambda: engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token),
            )
            try:
                assert entered.wait(timeout=5)
                revoking = pool.submit(
                    in_thread,
                    lambda: StepAttempt.objects.revoke(
                        attempt.pk,
                        lease_token=attempt.lease_token,
                        reason=LeaseRevocationReason.HEARTBEAT_LOST,
                        at=timezone.now(),
                    ),
                )
                with pytest.raises(FutureTimeoutError):
                    revoking.result(timeout=0.25)
            finally:
                release.set()
            assert executing.result(timeout=10)["executed"] == 1
            outcome = revoking.result(timeout=10)
        assert outcome.already_recorded and not outcome.revoked
        user.refresh_from_db()
        assert user.first_name == "committed"
        assert _retained_facts(attempt, step_run.run) == (
            before_artifacts + 1,
            before_advances + 1,
        )
    finally:
        release.set()
        _DatabaseCommandImpl.entered = None
        _DatabaseCommandImpl.release = None
