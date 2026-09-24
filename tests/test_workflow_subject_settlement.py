"""Terminal subject settlement retains native workflow delivery guarantees."""

from __future__ import annotations

from datetime import timedelta
from threading import Event, Thread, current_thread
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction
from django.test import override_settings
from django.utils import timezone
from rebac import system_context

from angee.workflows import engine, settlement
from angee.workflows.attempts import AttemptResultKind, LeaseRevocationReason
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.states import RunStatus
from angee.workflows.steps import StepResult, TransientStepError
from angee.workflows.tasks import consume_workflow_dispatch
from tests.workflows import (
    FixtureStep,
    StepAttempt,
    Workflow,
    WorkflowDispatch,
    advance_once,
    execute_started,
    start_run,
    step_run_for,
    workflow_with_steps,
)


def settle_fixture(run: Any) -> None:
    """Replaceable declared handler for tests of the real registration seam."""


def settle_unavailable(run: Any) -> None:
    """Fail delivery without consuming its retained dispatch."""

    raise RuntimeError("subject writer unavailable")


@pytest.fixture
def settlement_calls(settings: Any, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(f"{__name__}.settle_fixture", lambda run: calls.append(run.pk))
    settings.ANGEE_WORKFLOW_SUBJECT_SETTLERS = {
        "tests.workflows.Workflow": f"{__name__}.settle_fixture",
    }
    return calls


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("subject_kind", ["subjectless", "unregistered", "registered"])
def test_terminal_settlement_intent_requires_a_registered_subject(
    subject_kind: str,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
) -> None:
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    subject = None if subject_kind == "subjectless" else workflow
    if subject_kind == "unregistered":
        with system_context(reason="unregistered settlement subject"):
            subject = workflow.created_by
    run = start_run(workflow, subject=subject)
    with system_context(reason="terminal settlement registration boundary"):
        run.mark_failed()
        assert WorkflowDispatch.objects.filter(kind=WorkflowDispatchKind.RUN_SETTLE, run=run).count() == (
            1 if subject_kind == "registered" else 0
        )
    assert settlement_calls == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("outcome", ["success", "failure", "retry_exhaustion", "cancel"])
def test_every_engine_terminal_path_retains_one_subject_settlement(
    outcome: str,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = workflow_with_steps(
        steps=({"key": "page", "config": {"retry": {"max_attempts": 2, "backoff": {"wait": 3}}}},),
        edges=(),
    )
    run = start_run(workflow, subject=workflow)
    now = timezone.now()

    def perform(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        if outcome == "failure":
            raise RuntimeError("page failed")
        if outcome == "retry_exhaustion":
            raise TransientStepError("transport unavailable")
        return StepResult.done(output={"items": 3})

    monkeypatch.setattr(FixtureStep, "run", perform)
    advance_once(run, now=now)
    if outcome == "cancel":
        engine.cancel(run, actor=run.admission_actor_subject())
    else:
        execute_started(run, now=now)
        if outcome == "retry_exhaustion":
            row = step_run_for(run, "page")
            assert row.attempt == 2
            execute_started(run, now=now + timedelta(seconds=4))
        advance_once(run, now=now + timedelta(seconds=5))

    with system_context(reason="assert terminal subject settlement"):
        run.refresh_from_db()
        assert (
            run.status
            == {
                "success": RunStatus.SUCCEEDED,
                "failure": RunStatus.FAILED,
                "retry_exhaustion": RunStatus.FAILED,
                "cancel": RunStatus.CANCELED,
            }[outcome]
        )
        intent = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.RUN_SETTLE, run=run)
        assert intent.envelope.target_id == run.pk
        assert intent.consumed_at is None
        with transaction.atomic():
            duplicate, created = WorkflowDispatch.objects.schedule_run_settle(run)
        assert not created and duplicate.pk == intent.pk

    assert engine.settle_run_dispatch(intent.pk, expected_run_id=run.pk) == {"settled": 1}
    assert engine.settle_run_dispatch(intent.pk, expected_run_id=run.pk) == {"settled": 0}
    assert settlement_calls == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_transport_settles_registered_subject_once(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
) -> None:
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow, subject=workflow)
    with system_context(reason="subject settlement transport fixture"):
        run.mark_failed()
        intent = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.RUN_SETTLE, run=run)
        envelope = intent.envelope

    for _ in range(2):
        consume_workflow_dispatch.run(
            envelope.dispatch_id,
            kind=envelope.kind.value,
            target_id=envelope.target_id,
            generation=envelope.generation,
        )

    assert settlement_calls == [run.pk]
    with system_context(reason="inspect subject settlement transport delivery"):
        intent.refresh_from_db()
        assert intent.consumed_at is not None


@pytest.mark.django_db(transaction=True)
def test_failed_subject_settlement_preserves_pending_delivery(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
) -> None:
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow, subject=workflow)
    with system_context(reason="terminal subject settlement retry fixture"):
        run.mark_failed("failure")
        intent = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.RUN_SETTLE, run=run)

    with override_settings(
        ANGEE_WORKFLOW_SUBJECT_SETTLERS={"tests.workflows.Workflow": f"{__name__}.settle_unavailable"},
    ):
        with pytest.raises(RuntimeError, match="writer unavailable"):
            engine.settle_run_dispatch(intent.pk, expected_run_id=run.pk)
    with system_context(reason="retained delivery survives handler failure"):
        intent.refresh_from_db()
        assert intent.consumed_at is None
    assert settlement_calls == []
    assert engine.settle_run_dispatch(intent.pk, expected_run_id=run.pk) == {"settled": 1}
    assert settlement_calls == [run.pk]


@pytest.mark.django_db(transaction=True)
def test_subject_settlement_requires_terminal_transaction_and_exact_envelope(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
) -> None:
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow, subject=workflow)
    with pytest.raises(RuntimeError, match="terminal run transaction"):
        WorkflowDispatch.objects.schedule_run_settle(run)
    with transaction.atomic(), pytest.raises(ValidationError, match="terminal run"):
        WorkflowDispatch.objects.schedule_run_settle(run)
    with system_context(reason="subject settlement mismatched envelope fixture"):
        run.mark_failed()
        intent = WorkflowDispatch.objects.get(kind=WorkflowDispatchKind.RUN_SETTLE, run=run)
    with pytest.raises(ValidationError, match="envelope does not match"):
        engine.settle_run_dispatch(intent.pk, expected_run_id=run.pk + 1)
    with system_context(reason="mismatched settlement remains pending"):
        intent.refresh_from_db()
        assert intent.consumed_at is None


def test_settlement_registration_is_explicit_and_collisions_fail(
    settlement_calls: list[int],
) -> None:
    handlers = settlement.subject_settlers()
    assert set(handlers) == {(Workflow._meta.app_label, Workflow._meta.model_name)}
    with override_settings(ANGEE_WORKFLOW_SUBJECT_SETTLERS={}):
        assert settlement.subject_settlers() == {}
    assert settlement.subject_settlers() == handlers
    with (
        pytest.raises(ImproperlyConfigured, match="Multiple subject settlement handlers"),
        override_settings(
            ANGEE_WORKFLOW_SUBJECT_SETTLERS={
                "tests.workflows.Workflow": f"{__name__}.settle_fixture",
                "angee.workflows.models.Workflow": f"{__name__}.settle_fixture",
            }
        ),
    ):
        settlement.subject_settlers()


@pytest.mark.django_db(transaction=True)
def test_terminal_transition_and_subject_intent_roll_back_together(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    settlement_calls: list[int],
) -> None:
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow, subject=workflow)
    with system_context(reason="terminal transaction rollback fixture"):
        with pytest.raises(RuntimeError, match="rollback"), transaction.atomic():
            run.mark_failed("failure")
            assert WorkflowDispatch.objects.filter(kind=WorkflowDispatchKind.RUN_SETTLE, run=run).exists()
            raise RuntimeError("rollback")
        run.refresh_from_db()
        assert not run.is_terminal
        assert not WorkflowDispatch.objects.filter(kind=WorkflowDispatchKind.RUN_SETTLE, run=run).exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("body_fails", [False, True])
def test_bounded_io_heartbeats_the_captured_attempt_and_closes_its_worker(
    body_fails: bool,
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 0.09
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow)
    row = advance_once(run)[0]
    heartbeated = Event()
    heartbeats: list[int] = []
    workers: set[Thread] = set()
    invoking_thread = current_thread()
    manager_class = type(StepAttempt.objects)
    original = manager_class.heartbeat

    def heartbeat(manager: Any, attempt_id: int, **kwargs: Any) -> bool:
        result = original(manager, attempt_id, **kwargs)
        heartbeats.append(attempt_id)
        if current_thread() is not invoking_thread:
            workers.add(current_thread())
        if len(heartbeats) >= 2:
            heartbeated.set()
        return result

    def perform(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        with self.heartbeat_during(step_run):
            # Changing this caller's FK cache must never redirect the worker to
            # another lease. The durable attempt is still the admitted one.
            step_run.current_attempt_id = None
            assert heartbeated.wait(timeout=2)
            if body_fails:
                raise RuntimeError("bounded I/O failed")
        return StepResult.done(output={})

    monkeypatch.setattr(manager_class, "heartbeat", heartbeat)
    monkeypatch.setattr(FixtureStep, "run", perform)
    execute_started(run)

    assert len(heartbeats) >= 2
    assert set(heartbeats) == {row.current_attempt_id}
    assert workers and all(worker.daemon and not worker.is_alive() for worker in workers)
    assert step_run_for(run, "page").status == ("failed" if body_fails else "succeeded")


@pytest.mark.django_db(transaction=True)
def test_bounded_io_lost_lease_retains_transient_unapplied_result(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    settings: Any,
) -> None:
    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 0.09
    workflow = workflow_with_steps(steps=({"key": "page"},), edges=())
    run = start_run(workflow)
    row = advance_once(run)[0]
    rejected = Event()
    manager_class = type(StepAttempt.objects)
    original = manager_class.heartbeat

    def heartbeat(manager: Any, attempt_id: int, **kwargs: Any) -> bool:
        accepted = original(manager, attempt_id, **kwargs)
        if not accepted:
            rejected.set()
        return accepted

    def perform(self: FixtureStep, step_run: Any, *, now: Any) -> StepResult:
        with system_context(reason="capture heartbeat test lease"):
            attempt = step_run.current_attempt
        with self.heartbeat_during(step_run):
            StepAttempt.objects.revoke(
                attempt.pk,
                lease_token=attempt.lease_token,
                reason=LeaseRevocationReason.SUPERSEDED,
                at=now,
            )
            assert rejected.wait(timeout=2)
        return StepResult.done(output={})

    monkeypatch.setattr(manager_class, "heartbeat", heartbeat)
    monkeypatch.setattr(FixtureStep, "run", perform)
    execute_started(run)
    with system_context(reason="inspect retained lost-lease evidence"):
        attempt = row.current_attempt
        assert attempt.result_kind == AttemptResultKind.TRANSIENT_ERROR
        assert attempt.applied_at is None
        assert attempt.lease_revocation_reason == LeaseRevocationReason.SUPERSEDED
        assert "superseded" in attempt.error
