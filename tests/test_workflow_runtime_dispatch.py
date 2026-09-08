"""Focused integration tests for retained workflow runtime delivery."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from pydantic import BaseModel
from rebac import system_context

from angee.workflows import engine
from angee.workflows.attempts import AttemptResultKind
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepResult
from tests.workflows import Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun

pytest_plugins = ("tests.workflows",)


class _DoneImpl:
    input_model = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del now
        return StepResult.done({"seen": step_run.input}, outcome="ok")


class _WaitImpl:
    input_model = None
    until = timezone.now()

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del step_run, now
        return StepResult.wait(until=self.until, resume_state={"cursor": 1})


class _IntegerInput(BaseModel):
    value: int


class _ValidatedImpl:
    input_model = _IntegerInput


@pytest.mark.django_db(transaction=True)
def test_durable_advance_claims_and_exact_execute_retains_result(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    with system_context(reason="retained runtime setup"):
        workflow = Workflow.objects.create(name="Retained runtime", max_steps=10)
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
            input={"value": None},
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _DoneImpl)
    advance = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())

    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="retained runtime inspect"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert step_run.status == StepRunStatus.STARTED
    assert attempt.started_at is None

    assert engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token)["executed"] == 1
    with system_context(reason="retained runtime verify"):
        attempt.refresh_from_db()
        step_run.refresh_from_db()
    assert attempt.result_kind == str(AttemptResultKind.DONE)
    assert attempt.applied_at is not None
    assert attempt.output == {"seen": {"value": None}}
    assert step_run.status == StepRunStatus.SUCCEEDED
    with system_context(reason="retained runtime pending advance"):
        assert WorkflowDispatch.objects.filter(run=run, consumed_at__isnull=True).exists()


@pytest.mark.django_db(transaction=True)
def test_legacy_execute_payload_cannot_select_initialized_attempt(
    workflow_engine_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables
    with system_context(reason="retained legacy fence setup"):
        workflow = Workflow.objects.create(name="Legacy fence")
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=timezone.now()).attempt
    called = False

    def forbidden(_step_run_id: int) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(engine, "execute", forbidden)
    from angee.workflows.tasks import execute_workflow_step

    execute_workflow_step.run(step_run.pk)
    assert not called
    with system_context(reason="retained legacy fence verify"):
        attempt.refresh_from_db()
    assert attempt.started_at is None


@pytest.mark.django_db(transaction=True)
def test_wrong_dispatch_handler_does_not_consume_valid_execute_intent(
    workflow_engine_tables: None,
) -> None:
    del workflow_engine_tables
    with system_context(reason="retained mismatched envelope setup"):
        workflow = Workflow.objects.create(name="Mismatched envelope")
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=timezone.now()).attempt
    dispatch, _ = WorkflowDispatch.objects.schedule_execute(attempt)

    from angee.workflows.tasks import consume_workflow_dispatch

    with pytest.raises(ValidationError, match="Transport envelope"):
        consume_workflow_dispatch.run(
            dispatch.pk,
            WorkflowDispatchKind.ADVANCE.value,
            run.pk,
        )

    with system_context(reason="verify mismatched envelope remains pending"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is None
    assert engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)["executed"] == 1


@pytest.mark.django_db(transaction=True)
def test_wait_result_retains_immediate_and_future_advance_intents(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    now = timezone.now()
    _WaitImpl.until = now + timedelta(minutes=5)
    with system_context(reason="retained wait setup"):
        workflow = Workflow.objects.create(name="Retained wait", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            is_entry=True,
        )
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        step_run = StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _WaitImpl)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    engine.advance_dispatch(pulse.pk, now=now)
    with system_context(reason="load wait execution"):
        step_run.refresh_from_db()
        attempt = step_run.current_attempt
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)

    engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token, now=now)

    with system_context(reason="verify wait advances"):
        advances = list(
            WorkflowDispatch.objects.filter(
                run=run,
                kind=WorkflowDispatchKind.ADVANCE,
                consumed_at__isnull=True,
            ).order_by("available_at", "pk")
        )
    assert len(advances) == 2
    assert advances[0].available_at <= timezone.now()
    assert advances[1].available_at == _WaitImpl.until


@pytest.mark.django_db(transaction=True)
def test_preparation_failure_retains_candidate_provenance_and_advance(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del workflow_engine_tables, no_workflow_queue
    now = timezone.now()
    with system_context(reason="retained preparation setup"):
        workflow = Workflow.objects.create(name="Preparation evidence", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="start",
            name="Start",
            step_class="agent_session",
            input_binding={"kind": "workflow_input"},
            is_entry=True,
        )
        run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            input_present=True,
            input={"value": "not-an-int"},
        )
        StepRun.objects.create(run=run, step=step, status=StepRunStatus.SCHEDULED)
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _ValidatedImpl)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    assert engine.advance_dispatch(pulse.pk, now=now)["claimed"] == 1

    with system_context(reason="verify preparation evidence"):
        step_run = StepRun.objects.get(run=run, step=step)
        attempt = step_run.current_attempt
    assert step_run.status == StepRunStatus.FAILED
    assert attempt.started_at is None
    assert attempt.result_kind == str(AttemptResultKind.PREPARATION_ERROR)
    assert attempt.input_present is True
    assert attempt.input == {"value": "not-an-int"}
    assert attempt.input_provenance == {
        "kind": "workflow_input",
        "run_id": run.pk,
        "path": [],
    }
    assert "value" in (attempt.stacktrace or "")
    with system_context(reason="verify preparation wake"):
        assert WorkflowDispatch.objects.filter(
            run=run,
            kind=WorkflowDispatchKind.ADVANCE,
            consumed_at__isnull=True,
        ).exists()
