"""Focused contracts for durable workflow dispatch admission and delivery."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import OperationalError, models, transaction
from django.db.models.signals import post_save
from django.utils import timezone
from rebac import system_context

from angee.base.refs import canonical_record_target
from angee.workflows import engine
from angee.workflows.attempts import AttemptResult, AttemptResultKind, LeaseRevocationReason
from angee.workflows.dispatch import (
    DISPATCH_KINDS,
    DispatchTarget,
    WorkflowDispatchEnvelope,
    WorkflowDispatchKind,
    dispatch_constraints,
    publish_due,
)
from tests.workflows import Decision, Step, StepAttempt, StepRun, Workflow, WorkflowDispatch, WorkflowRun


@pytest.fixture()
def run(workflow_engine_tables: None) -> WorkflowRun:
    with system_context(reason="dispatch test run"):
        workflow = Workflow.objects.create(name="Dispatch owner")
        Step.objects.create(workflow=workflow, key="start", name="Start", step_class="agent_session", is_entry=True)
        return WorkflowRun.objects.create(workflow=workflow)


@pytest.mark.django_db(transaction=True)
def test_artifact_facade_accepts_a_canonical_target_without_an_explicit_alias(run: WorkflowRun) -> None:
    with system_context(reason="canonical artifact delivery"), transaction.atomic():
        target = canonical_record_target(run, using="default")
        dispatch = engine.schedule_artifact_delivery(target)
    assert dispatch._state.db == "default"
    assert dispatch.artifact_content_type_id == target.content_type.pk
    assert dispatch.artifact_object_id == target.object_id


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
def test_advance_error_is_visible_until_the_exact_durable_intent_retries(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    route = engine._route_completed_steps
    failed = False

    def fail_once(active_run: WorkflowRun, *, alias: str) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise ValidationError("Map evidence is structurally invalid.")
        route(active_run, alias=alias)

    with patch.object(engine, "_route_completed_steps", side_effect=fail_once):
        with pytest.raises(ValidationError, match="Map evidence is structurally invalid"):
            engine.advance_dispatch(dispatch.pk, expected_run_id=run.pk, now=now)

        with system_context(reason="verify retained advance error"):
            run.refresh_from_db()
            dispatch.refresh_from_db()
        assert run.status == "pending"
        assert run.error == (
            f"Workflow advancement {dispatch.sqid} could not continue: ['Map evidence is structurally invalid.']"
        )
        assert dispatch.consumed_at is None

        assert engine.advance_dispatch(dispatch.pk, expected_run_id=run.pk, now=now) == {"claimed": 0}

    with system_context(reason="verify successful advance retry"):
        run.refresh_from_db()
        dispatch.refresh_from_db()
    assert run.status == "running"
    assert run.error == ""
    assert dispatch.consumed_at == now

    masked = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    with (
        patch.object(
            engine,
            "_route_completed_steps",
            side_effect=ValidationError("Original advance failure."),
        ),
        patch.object(
            WorkflowDispatch.objects,
            "record_advance_error",
            side_effect=RuntimeError("Telemetry unavailable."),
        ),
    ):
        with pytest.raises(ValidationError, match="Original advance failure"):
            engine.advance_dispatch(masked.pk, expected_run_id=run.pk, now=now)
    assert engine.advance_dispatch(masked.pk, expected_run_id=run.pk, now=now) == {"claimed": 0}

    blocked = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    other = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    assert WorkflowDispatch.objects.record_advance_error(
        blocked.pk, error=ValidationError("A different pending advance failed.")
    )
    assert engine.advance_dispatch(other.pk, expected_run_id=run.pk, now=now) == {"claimed": 0}
    with system_context(reason="verify exact advance error ownership"):
        run.refresh_from_db()
    assert str(blocked.sqid) in run.error
    assert engine.advance_dispatch(blocked.pk, expected_run_id=run.pk, now=now) == {"claimed": 0}
    with system_context(reason="verify exact advance error clearance"):
        run.refresh_from_db()
    assert run.error == ""

    with system_context(reason="terminal run error fixture"):
        run.mark_failed("Retained domain failure.")
    terminal = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    assert engine.advance_dispatch(terminal.pk, expected_run_id=run.pk, now=now) == {"claimed": 0}
    with system_context(reason="verify terminal run error"):
        run.refresh_from_db()
    assert run.error == "Retained domain failure."

    with system_context(reason="cancel a run while its advance is retrying"):
        canceled_run = WorkflowRun.objects.create(workflow=run.workflow)
    pending = WorkflowDispatch.objects.schedule_advance(canceled_run, available_at=now)
    assert WorkflowDispatch.objects.record_advance_error(
        pending.pk,
        error=ValidationError("Retained before cancellation."),
    )
    with system_context(reason="retain active error through cancellation"):
        canceled_run.refresh_from_db()
        retained_error = canceled_run.error
        canceled_run.mark_canceled()
    assert engine.advance_dispatch(pending.pk, expected_run_id=canceled_run.pk, now=now) == {"claimed": 0}
    with system_context(reason="verify fenced delivery preserves the same error marker"):
        canceled_run.refresh_from_db()
        pending.refresh_from_db()
    assert canceled_run.status == "canceled"
    assert canceled_run.error == retained_error
    assert pending.consumed_at == now


@pytest.mark.django_db(transaction=True)
def test_publication_bulk_loads_execution_envelopes(run: WorkflowRun, django_assert_num_queries: Any) -> None:
    now = timezone.now()
    intents = []
    with system_context(reason="execution publication batch"):
        step = run.workflow.steps.get(key="start")
        other = WorkflowRun.objects.create(workflow=run.workflow)
        for current in (run, other):
            slot = current.step_runs.create(step=step, status="scheduled")
            attempt = StepAttempt.objects.claim(slot, claimed_at=now).attempt
            intent, _ = WorkflowDispatch.objects.schedule_execute(attempt)
            intents.append((intent.pk, attempt.pk, attempt.lease_token))
    # One system-context audit insert and one SELECT, independent of batch size.
    with django_assert_num_queries(2):
        envelopes = WorkflowDispatch.objects.due_envelopes(now=now, limit=10)
    assert [(item.dispatch_id, item.target_id, item.lease_token) for item in envelopes] == intents


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

    assert WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=attempt.pk) == {"executed": 0}
    with system_context(reason="verify wrong lease remains pending"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is None
    assert WorkflowDispatch.objects.deliver(
        dispatch.pk,
        expected_target_id=attempt.pk,
        lease_token=attempt.lease_token,
    ) == {"executed": 0}
    with system_context(reason="verify completed execution is fenced"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("result", "revocation_reason"),
    [
        (AttemptResult(AttemptResultKind.DONE), LeaseRevocationReason.CANCELED),
        (AttemptResult(AttemptResultKind.ERROR, error="Late failure"), LeaseRevocationReason.SUPERSEDED),
    ],
)
def test_dispatch_and_attempt_fields_hydrate_native_enums(
    run: WorkflowRun,
    result: AttemptResult,
    revocation_reason: LeaseRevocationReason,
) -> None:
    now = timezone.now()
    with system_context(reason="native workflow enum fixture"):
        step_run = run.step_runs.create(step=run.workflow.steps.get(key="start"), status="scheduled")
    attempt = StepAttempt.objects.claim(step_run, claimed_at=now).attempt
    dispatch, _ = WorkflowDispatch.objects.schedule_execute(attempt)
    with system_context(reason="native workflow empty enum roundtrip"):
        attempt.refresh_from_db()
        dispatch.refresh_from_db()
    assert dispatch.kind is WorkflowDispatchKind.EXECUTE
    assert attempt.result_kind == ""
    assert attempt.lease_revocation_reason == ""

    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=now)
    StepAttempt.objects.revoke(attempt.pk, lease_token=attempt.lease_token, reason=revocation_reason, at=now)
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=result,
        recorded_at=now,
    )
    with system_context(reason="native workflow enum roundtrip"):
        attempt.refresh_from_db()
    assert attempt.result_kind is result.kind
    assert attempt.lease_revocation_reason is revocation_reason


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
def test_direct_dispatch_mutations_and_invalid_targets_are_rejected(run: WorkflowRun) -> None:
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=timezone.now())
    dispatch.send_count = 99
    with pytest.raises(TypeError, match="WorkflowDispatchManager"):
        dispatch.save(update_fields=["send_count"])
    with pytest.raises(TypeError, match="collection updates"):
        WorkflowDispatch.objects.filter(pk=dispatch.pk).update(send_count=99)
    with pytest.raises(TypeError, match="bulk_create"):
        WorkflowDispatch.objects.bulk_create([dispatch])
    with pytest.raises(TypeError, match="bulk_update"):
        WorkflowDispatch.objects.bulk_update([dispatch], ["send_count"])
    queryset = WorkflowDispatch.objects.filter(pk=dispatch.pk)
    with pytest.raises(TypeError, match="durable delivery evidence"):
        queryset.delete()
    with pytest.raises(TypeError, match="durable delivery evidence"):
        queryset._raw_delete(using=queryset.db)
    with pytest.raises(ValidationError, match="envelope does not match"):
        WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk + 1000)
    with system_context(reason="verify invalid target remains pending"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is None


@pytest.mark.django_db(transaction=True)
def test_exact_owner_consumption_leaves_early_intent_pending(run: WorkflowRun) -> None:
    available_at = timezone.now() + timedelta(minutes=5)
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=available_at)
    assert WorkflowDispatch.objects.deliver(
        dispatch.pk,
        expected_target_id=run.pk,
        now=available_at - timedelta(microseconds=1),
    ) == {"claimed": 0}
    with system_context(reason="verify early dispatch"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at is None

    WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=available_at)
    with system_context(reason="verify due dispatch"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at == available_at


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

    dispatch, created = WorkflowDispatch.objects.schedule_decision(WorkflowDispatchKind.DECISION_EXPIRE, decision)
    duplicate, duplicate_created = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )

    assert created and not duplicate_created and duplicate.pk == dispatch.pk
    assert dispatch.generation == 2
    assert dispatch.available_at == decision.expires_at
    with system_context(reason="resolve dispatch decision"):
        assert decision.expire(at=decision.expires_at, generation=decision.attempts, due=True)
    after_resolution, created_after_resolution = WorkflowDispatch.objects.schedule_decision(
        WorkflowDispatchKind.DECISION_EXPIRE, decision
    )
    assert not created_after_resolution and after_resolution.pk == dispatch.pk


@pytest.mark.django_db(transaction=True)
def test_publication_records_send_that_is_consumed_before_sender_returns(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)

    def consume(envelope: WorkflowDispatchEnvelope) -> None:
        WorkflowDispatch.objects.deliver(envelope.dispatch_id, expected_target_id=envelope.target_id, now=now)

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
    with patch.object(engine, "advance_locked", wraps=engine.advance_locked) as handler:
        WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=now)
        assert WorkflowDispatch.objects.deliver(
            dispatch.pk,
            expected_target_id=run.pk,
            now=now,
        ) == {"claimed": 0}
    assert handler.call_count == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("handled", [False, True])
def test_delivery_consumes_handled_and_fenced_intents(run: WorkflowRun, handled: bool) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    with patch.object(engine, "advance_locked", return_value=handled) as handler:
        WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=now)
    assert handler.call_count == 1
    with system_context(reason="verify dispatcher consumes handler outcome"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at == now


@pytest.mark.django_db(transaction=True)
def test_failed_consume_cannot_commit_owner_changes(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    original_deliveries = run.deliveries

    def mutate(target: DispatchTarget, *, at: datetime) -> bool:
        target.row.deliveries += 1
        target.row.save(using=target.row._state.db, update_fields=["deliveries", "updated_at"])
        return True

    with (
        patch.object(engine, "advance_locked", side_effect=mutate),
        patch.object(type(WorkflowDispatch.objects.get_queryset()), "_consume", return_value=0),
        pytest.raises(RuntimeError, match="already consumed|admission changed"),
    ):
        WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=now)

    with system_context(reason="verify failed consume rollback"):
        run.refresh_from_db()
        dispatch.refresh_from_db()
    assert run.deliveries == original_deliveries
    assert dispatch.consumed_at is None


@pytest.mark.django_db(transaction=True)
def test_dispatch_telemetry_signal_cannot_reconsume_or_save(run: WorkflowRun) -> None:
    now = timezone.now()
    dispatch = WorkflowDispatch.objects.schedule_advance(run, available_at=now)
    rejected: list[str] = []

    def attack(sender: type[WorkflowDispatch], instance: WorkflowDispatch, **kwargs: object) -> None:
        del sender, kwargs
        assert WorkflowDispatch.objects.deliver(instance.pk, expected_target_id=run.pk, now=now) == {"claimed": 0}
        rejected.append("duplicate")
        try:
            instance.save(update_fields=["updated_at"])
        except TypeError:
            rejected.append("save")

    post_save.connect(attack, sender=WorkflowDispatch, weak=False)
    try:
        WorkflowDispatch.objects.deliver(dispatch.pk, expected_target_id=run.pk, now=now)
        WorkflowDispatch.objects.record_publication(dispatch.pk, attempted_at=now, error="")
    finally:
        post_save.disconnect(attack, sender=WorkflowDispatch)

    assert rejected == ["duplicate", "save"]


@pytest.mark.django_db(transaction=True)
def test_owner_preflight_rejects_ancestry_drift_during_locking(run: WorkflowRun) -> None:
    with system_context(reason="dispatch ancestry drift setup"):
        step = run.workflow.steps.get(key="start")
        step_run = run.step_runs.create(step=step, status="scheduled")
        other_run = WorkflowRun.objects.create(workflow=run.workflow)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=timezone.now()).attempt
    dispatch, _ = WorkflowDispatch.objects.schedule_execute(attempt)

    from angee.workflows import managers as workflow_managers

    native_system_queryset = workflow_managers.system_queryset
    drifted = False

    def drift_before_run_lock(model: type[Any], **kwargs: Any) -> Any:
        nonlocal drifted
        if model is WorkflowRun and kwargs.get("lock") == ("self",) and not drifted:
            drifted = True
            models.QuerySet.update(
                native_system_queryset(StepRun, using="default", lock=None).filter(pk=step_run.pk),
                run_id=other_run.pk,
            )
        return native_system_queryset(model, **kwargs)

    with patch.object(workflow_managers, "system_queryset", side_effect=drift_before_run_lock):
        with pytest.raises(OperationalError, match="ancestry changed"):
            WorkflowDispatch.objects.deliver(
                dispatch.pk,
                expected_target_id=attempt.pk,
                lease_token=attempt.lease_token,
            )


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
    assert set(DISPATCH_KINDS) == set(WorkflowDispatchKind)
    assert all(kind.spec is DISPATCH_KINDS[kind] for kind in WorkflowDispatchKind)


def deliver_declared_spec(target: DispatchTarget, *, at: datetime) -> bool:
    """One test declaration changes its target, result, and handler together."""

    assert target.row.pk == target.dispatch.pk
    assert target.dispatch.available_at <= at
    target.result["selected"] = 1
    return True


@pytest.mark.django_db(transaction=True)
def test_one_kind_spec_drives_constraints_envelope_and_delivery(
    run: WorkflowRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = timezone.now()
    candidates = [WorkflowDispatch.objects.schedule_advance(run, available_at=now) for _ in range(2)]
    dispatch = next(candidate for candidate in candidates if candidate.pk != run.pk)
    monkeypatch.setitem(
        DISPATCH_KINDS,
        WorkflowDispatchKind.ADVANCE,
        replace(
            WorkflowDispatchKind.ADVANCE.spec,
            target_field="pk",
            lock_plan=(),
            uniqueness=("run",),
            handler_path=f"{__name__}.deliver_declared_spec",
            result_fields=("selected",),
        ),
    )

    assert dispatch.envelope.target_id == dispatch.pk
    constraints = dispatch_constraints()
    with system_context(reason="validate declared dispatch shape and uniqueness"):
        constraints[0].validate(WorkflowDispatch, dispatch, using="default")
        unique = next(constraint for constraint in constraints if constraint.name == "uniq_wfd_advance")
        with pytest.raises(ValidationError):
            unique.validate(WorkflowDispatch, dispatch, using="default")
    assert WorkflowDispatch.objects.deliver(
        dispatch.pk,
        expected_target_id=dispatch.pk,
        now=now,
    ) == {"selected": 1}
    with system_context(reason="verify declared delivery consumed intent"):
        dispatch.refresh_from_db()
    assert dispatch.consumed_at == now
