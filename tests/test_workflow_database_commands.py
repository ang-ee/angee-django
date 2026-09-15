"""Database command effects and attempt finalization share one fenced transaction."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from threading import Event
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import ArtifactSpec, InvocationAdmission, LeaseRevocationReason
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepEffect, StepExecutionMode, StepResult
from tests.workflows import Step, StepArtifact, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun

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


def _scheduled_command(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
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
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _DatabaseCommandImpl)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(pulse.pk)["claimed"] == 1
    with system_context(reason="database command test inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    return user, step_run, attempt, dispatch


@pytest.mark.django_db(transaction=True)
def test_legacy_step_run_cannot_execute_a_database_command_without_a_fence(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    user = User.objects.create_user(username="wf-command-legacy", first_name="original")
    with system_context(reason="legacy command guard setup"):
        workflow = Workflow.objects.create(name="Legacy command guard", max_steps=10)
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
            status=StepRunStatus.STARTED,
            input={"user_id": user.pk},
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _DatabaseCommandImpl)

    with pytest.raises(ValidationError, match="retained attempt"):
        engine.execute(step_run.pk)

    user.refresh_from_db()
    with system_context(reason="verify legacy command guard"):
        step_run.refresh_from_db()
    assert user.first_name == "original"
    assert step_run.attempt == 0


@pytest.mark.django_db(transaction=True)
def test_revoked_after_invocation_admission_is_fenced_before_domain_write(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
def test_continuation_failure_rolls_back_command_result_and_artifact_before_failure_audit(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL row-lock contract")
def test_revoke_waits_for_fenced_command_and_observes_committed_result(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
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
