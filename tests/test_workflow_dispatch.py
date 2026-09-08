"""Focused contracts for unused durable workflow dispatch."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.db import OperationalError, transaction
from django.db.models.signals import post_save
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import AttemptResult, AttemptResultKind
from angee.workflows.dispatch import (
    DispatchConsumption,
    DispatchPreflightDisposition,
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
    publish_due,
)
from tests.workflows import Decision, Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun

pytest_plugins = ("tests.workflows",)


@pytest.fixture()
def run(workflow_engine_tables: None) -> WorkflowRun:
    with system_context(reason="dispatch test run"):
        workflow = Workflow.objects.create(name="Dispatch owner")
        Step.objects.create(workflow=workflow, key="start", name="Start", step_class="agent_session", is_entry=True)
        return WorkflowRun.objects.create(workflow=workflow)


@pytest.mark.django_db(transaction=True)
def test_advance_intents_are_independent_and_publish_without_consumption(run: WorkflowRun) -> None:
    now = timezone.now()
    immediate = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    future = WorkflowDispatch.objects.schedule_advance(run, available_at=now + timedelta(hours=1))
    sent: list[WorkflowDispatchEnvelope] = []

    result = publish_due(sent.append, now=now, limit=10)

    assert result == {"selected": 1, "sent": 1, "failed": 0}
    assert sent[0].dispatch_id == immediate.pk
    with system_context(reason="verify dispatch telemetry"):
        immediate.refresh_from_db()
        future.refresh_from_db()
    assert immediate.send_count == 1 and immediate.consumed_at is None
    assert future.send_count == 0 and future.consumed_at is None


@pytest.mark.django_db(transaction=True)
def test_execute_dispatch_uses_attempt_availability_and_exact_lease(run: WorkflowRun) -> None:
    with system_context(reason="dispatch test slot"):
        step_run = run.step_runs.create(step=run.workflow.steps.get(key="start"), status="scheduled")
    attempt = StepAttempt.objects.claim(step_run, claimed_at=timezone.now()).attempt

    dispatch, created = WorkflowDispatch.objects.schedule_execute(attempt)
    duplicate, duplicate_created = WorkflowDispatch.objects.schedule_execute(attempt)

    assert created and not duplicate_created and duplicate.pk == dispatch.pk
    assert dispatch.available_at == attempt.claimed_at
    assert dispatch.envelope.lease_token == attempt.lease_token

    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=attempt.claimed_at)
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.DONE),
        recorded_at=attempt.claimed_at,
    )
    after_completion, created_after_completion = WorkflowDispatch.objects.schedule_execute(attempt)
    assert not created_after_completion and after_completion.pk == dispatch.pk

    with transaction.atomic():
        with WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk,
            lease_token=None,
            at=timezone.now(),
            using="default",
        ) as preflight:
            assert preflight.disposition == DispatchPreflightDisposition.FENCED


@pytest.mark.django_db(transaction=True)
def test_publish_due_rejects_ambient_transactions_and_bounds_transport_errors(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    with transaction.atomic(), pytest.raises(RuntimeError, match="cannot run inside"):
        publish_due(lambda envelope: None, now=now)

    publish_due(lambda envelope: (_ for _ in ()).throw(RuntimeError("secret credential")), now=now)
    with system_context(reason="verify bounded dispatch error"):
        dispatch.refresh_from_db()
    assert dispatch.last_send_error == "Transport send failed."
    assert "secret" not in dispatch.last_send_error


@pytest.mark.django_db(transaction=True)
def test_direct_dispatch_mutations_and_unowned_consumption_are_rejected(run: WorkflowRun) -> None:
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    dispatch.send_count = 99
    with pytest.raises(TypeError, match="WorkflowDispatchManager"):
        dispatch.save(update_fields=["send_count"])
    with pytest.raises(TypeError, match="collection updates"):
        WorkflowDispatch.objects.filter(pk=dispatch.pk).update(send_count=99)
    with pytest.raises(RuntimeError, match="domain-owner authority"):
        WorkflowDispatch.objects._consume_locked(dispatch.pk, at=timezone.now())


@pytest.mark.django_db(transaction=True)
def test_exact_owner_consumption_leaves_early_intent_pending(run: WorkflowRun) -> None:
    available_at = timezone.now() + timedelta(minutes=5)
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=available_at)
    with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
        dispatch_id=dispatch.pk,
        lease_token=None,
        at=available_at - timedelta(microseconds=1),
        using="default",
    ) as preflight:
        assert preflight.disposition == DispatchPreflightDisposition.EARLY
    with system_context(reason="verify early dispatch"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is None

    with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
        dispatch_id=dispatch.pk,
        lease_token=None,
        at=available_at,
        using="default",
    ) as preflight:
        assert preflight.disposition == DispatchPreflightDisposition.READY
        assert WorkflowDispatch.objects._consume_locked(
            dispatch.pk, at=available_at
        ) == DispatchConsumption.CONSUMED


@pytest.mark.django_db(transaction=True)
def test_decision_timer_uses_locked_native_deadline_and_generation(run: WorkflowRun) -> None:
    with system_context(reason="dispatch decision setup"):
        step_run = run.step_runs.create(step=run.workflow.steps.get(key="start"), status="waiting")
        decision = Decision.objects.create(
            step_run=step_run,
            action="approve",
            attempts=2,
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    dispatch, created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )
    duplicate, duplicate_created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )

    assert created and not duplicate_created and duplicate.pk == dispatch.pk
    assert dispatch.generation == 2
    assert dispatch.available_at == decision.expires_at
    with system_context(reason="resolve dispatch decision"):
        decision.mark_expired()
    after_resolution, created_after_resolution = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )
    assert not created_after_resolution and after_resolution.pk == dispatch.pk


@pytest.mark.django_db(transaction=True)
def test_publication_records_send_that_is_consumed_before_sender_returns(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    def consume(_envelope: WorkflowDispatchEnvelope) -> None:
        with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk,
            lease_token=None,
            at=now,
            using="default",
        ) as preflight:
            assert preflight.disposition == DispatchPreflightDisposition.READY
            assert WorkflowDispatch.objects._consume_locked(
                dispatch.pk, at=now
            ) == DispatchConsumption.CONSUMED

    publish_due(consume, now=now)
    with system_context(reason="verify consumed publication telemetry"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at == now
    assert dispatch.send_count == 1
    assert dispatch.last_sent_at == now


@pytest.mark.django_db(transaction=True)
def test_duplicate_is_preflighted_before_owner_mutation(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
        dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
    ) as preflight:
        assert preflight.disposition == DispatchPreflightDisposition.READY
        WorkflowDispatch.objects._consume_locked(dispatch.pk, at=now)

    with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
        dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
    ) as preflight:
        assert preflight.disposition == DispatchPreflightDisposition.DUPLICATE


@pytest.mark.django_db(transaction=True)
def test_ready_owner_must_consume_or_roll_back(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    with pytest.raises(RuntimeError, match="without consuming"):
        with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
        ) as preflight:
            assert preflight.disposition == DispatchPreflightDisposition.READY


@pytest.mark.django_db(transaction=True)
def test_failed_consume_cannot_spend_authority_or_commit_owner_changes(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    original_deliveries = run.deliveries

    with pytest.raises(RuntimeError, match="without consuming"):
        with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
        ) as preflight:
            assert preflight.disposition == DispatchPreflightDisposition.READY
            with system_context(reason="simulate dispatch owner mutation"):
                run.deliveries += 1
                run.save(update_fields=["deliveries", "updated_at"])
            with pytest.raises(RuntimeError, match="authority"):
                WorkflowDispatch.objects._consume_locked(dispatch.pk + 1000, at=now)

    with system_context(reason="verify failed consume rollback"):
        run.refresh_from_db()
        dispatch.refresh_from_db()
    assert run.deliveries == original_deliveries
    assert dispatch.consumed_at is None


@pytest.mark.django_db(transaction=True)
def test_dispatch_save_signal_cannot_reuse_consume_or_save_capability(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    rejected: list[str] = []

    def attack(sender: type[WorkflowDispatch], instance: WorkflowDispatch, **kwargs: object) -> None:
        del sender, kwargs
        try:
            WorkflowDispatch.objects._consume_locked(instance.pk, at=now)
        except RuntimeError:
            rejected.append("consume")
        try:
            instance.save(update_fields=["updated_at"])
        except TypeError:
            rejected.append("save")

    post_save.connect(attack, sender=WorkflowDispatch, weak=False)
    try:
        with transaction.atomic(), WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk, lease_token=None, at=now, using="default"
        ) as preflight:
            assert preflight.disposition == DispatchPreflightDisposition.READY
            WorkflowDispatch.objects._consume_locked(dispatch.pk, at=now)
    finally:
        post_save.disconnect(attack, sender=WorkflowDispatch)

    assert rejected == ["consume", "save"]


@pytest.mark.django_db(transaction=True)
def test_owner_preflight_rejects_ancestry_drift_during_locking(run: WorkflowRun) -> None:
    with system_context(reason="dispatch ancestry drift setup"):
        step = run.workflow.steps.get(key="start")
        step_run = run.step_runs.create(step=step, status="scheduled")
        other_run = WorkflowRun.objects.create(workflow=run.workflow)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=timezone.now()).attempt
    dispatch, _ = WorkflowDispatch.objects.schedule_execute(attempt)

    from angee.workflows import models as workflow_models

    native_system_queryset = workflow_models.system_queryset
    drifted = False

    def drift_before_run_lock(model: type[Any], **kwargs: Any) -> Any:
        nonlocal drifted
        if model is WorkflowRun and kwargs.get("lock") == ("self",) and not drifted:
            drifted = True
            native_system_queryset(StepRun, using="default", lock=None).filter(pk=step_run.pk).update(
                run_id=other_run.pk
            )
        return native_system_queryset(model, **kwargs)

    with patch.object(workflow_models, "system_queryset", side_effect=drift_before_run_lock):
        with transaction.atomic(), pytest.raises(OperationalError, match="ancestry changed"):
            with WorkflowDispatch.objects._owner_transition(
                dispatch_id=dispatch.pk,
                lease_token=attempt.lease_token,
                at=timezone.now(),
                using="default",
            ):
                pytest.fail("drifted ancestry must not issue preflight authority")


@pytest.mark.django_db(transaction=True)
def test_publication_telemetry_keeps_latest_attempt_facts(run: WorkflowRun) -> None:
    older = timezone.now()
    newer = older + timedelta(seconds=10)
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=older)

    WorkflowDispatch.objects.record_publication(dispatch.pk, attempted_at=newer, error="")
    WorkflowDispatch.objects.record_publication(dispatch.pk, attempted_at=older, error="failed")

    with system_context(reason="verify monotonic dispatch telemetry"):
        dispatch.refresh_from_db()
    assert dispatch.send_count == 2
    assert dispatch.last_sent_at == newer
    assert dispatch.last_send_error == ""
    assert dispatch.next_send_at == newer + timedelta(seconds=30)


def test_dispatch_kind_is_closed() -> None:
    assert {kind.value for kind in WorkflowDispatchKind} == {
        "advance", "execute", "decision_expire", "decision_escalate",
    }
