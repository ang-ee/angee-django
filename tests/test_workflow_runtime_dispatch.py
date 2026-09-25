"""Focused integration tests for retained workflow runtime delivery."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from pydantic import BaseModel
from rebac import system_context

from angee.testing.models import Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun
from angee.workflows import engine
from angee.workflows.attempts import AttemptResultKind, DecisionGateOutput
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import StepImpl, StepResult


class _DoneImpl(StepImpl):
    input_model = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del now
        return StepResult.done({"seen": step_run.input}, outcome="ok")


class _EmptyErrorImpl(StepImpl):
    input_model = None

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del step_run, now
        raise StopIteration


class _WaitImpl(StepImpl):
    input_model = None
    until = timezone.now()

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        del step_run, now
        return StepResult.wait(until=self.until, resume_state={"cursor": 1})


class _IntegerInput(BaseModel):
    value: int


class _ValidatedImpl(StepImpl):
    input_model = _IntegerInput


class _DecisionGateConsumer(StepImpl):
    input_model = DecisionGateOutput


@pytest.mark.django_db(transaction=True)
def test_durable_advance_claims_and_exact_execute_retains_result(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
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
def test_empty_exception_message_retains_class_and_traceback(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
    with system_context(reason="empty exception result setup"):
        workflow = Workflow.objects.create(name="Empty exception result", max_steps=10)
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
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _EmptyErrorImpl)
    advance = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    assert engine.advance_dispatch(advance.pk)["claimed"] == 1
    with system_context(reason="empty exception result execution"):
        step_run.refresh_from_db()
        attempt = StepAttempt.objects.get(pk=step_run.current_attempt_id)
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)

    assert engine.execute_dispatch(execute.pk, attempt.pk, attempt.lease_token)["executed"] == 1

    with system_context(reason="empty exception result verification"):
        attempt.refresh_from_db()
    assert attempt.result_kind == str(AttemptResultKind.ERROR)
    assert attempt.error == "StopIteration"
    assert "StopIteration" in attempt.stacktrace


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("delay", [0, 60])
def test_advance_wake_is_durable_before_transport_publication(
    composed_tables: None,
    monkeypatch: pytest.MonkeyPatch,
    delay: int,
) -> None:
    """Immediate and timer wakes commit their intent before notifying transport."""

    del composed_tables
    now = timezone.now()
    published: list[int] = []
    with system_context(reason="durable wake setup"):
        workflow = Workflow.objects.create(name="Durable wake")
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
    monkeypatch.setattr(engine.timezone, "now", lambda: now)
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: published.append(run.pk))

    with transaction.atomic():
        if delay:
            engine.enqueue_advance_at(run.pk, now + timedelta(seconds=delay))
        else:
            engine.enqueue_advance(run.pk)
        with system_context(reason="inspect durable wake before commit"):
            dispatch = WorkflowDispatch.objects.get(run=run)
        assert dispatch.kind == WorkflowDispatchKind.ADVANCE
        assert dispatch.available_at == now + timedelta(seconds=delay)
        assert published == []
    assert published == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_wrong_dispatch_handler_does_not_consume_valid_execute_intent(
    composed_tables: None,
) -> None:
    del composed_tables
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
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
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
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, no_workflow_queue
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


@pytest.mark.django_db(transaction=True)
def test_preparation_validates_persisted_json_through_the_input_model_json_boundary(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict tuple contracts accept the JSON arrays retained by JSONField."""

    del composed_tables, no_workflow_queue
    now = timezone.now()
    gate_output = {"resolutions": [], "outcome": "completed"}
    with system_context(reason="JSON input model boundary setup"):
        workflow = Workflow.objects.create(name="JSON input model boundary", max_steps=10)
        step = Step.objects.create(
            workflow=workflow,
            key="consume",
            name="Consume gate",
            step_class="agent_session",
            input_binding={"kind": "workflow_input"},
            is_entry=True,
        )
        run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            input_present=True,
            input=gate_output,
        )
        step_run = StepRun.objects.create(
            run=run,
            step=step,
            status=StepRunStatus.SCHEDULED,
        )
    monkeypatch.setattr(type(step), "resolve_impl", lambda self, field: _DecisionGateConsumer)
    pulse = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    assert engine.advance_dispatch(pulse.pk, now=now)["claimed"] == 1

    with system_context(reason="JSON input model boundary assertion"):
        step_run.refresh_from_db()
        attempt = step_run.current_attempt
        execute = WorkflowDispatch.objects.get(step_attempt=attempt)
    assert step_run.status == StepRunStatus.STARTED
    assert attempt.input == gate_output
    assert attempt.result_recorded_at is None
    assert execute.kind == WorkflowDispatchKind.EXECUTE
    assert _DecisionGateConsumer.validate_input(attempt.input).resolutions == ()
