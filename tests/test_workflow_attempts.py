"""Focused contracts for retained workflow execution attempts."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import (
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    LeaseRevocationReason,
)
from angee.workflows.models import RunStatus, StepRunStatus
from tests.workflows import StepAttempt, StepRun, WorkflowRun, workflow_with_steps

pytest_plugins = ("tests.workflows",)
User = get_user_model()


@pytest.fixture()
def scheduled_step_run(workflow_engine_tables: None) -> StepRun:
    del workflow_engine_tables
    workflow = workflow_with_steps(steps=({"key": "start", "step_class": "agent_session"},), edges=())
    with system_context(reason="test retained attempt setup"):
        run = WorkflowRun.objects.create(workflow=workflow, status=RunStatus.RUNNING)
        return StepRun.objects.create(run=run, step=workflow.steps.get(key="start"))


@pytest.mark.django_db(transaction=True)
def test_allocate_initializes_stable_effect_identity_and_preserves_presence(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(
        scheduled_step_run,
        input=AttemptInput(present=True, value=None, provenance={"source": "constant"}),
        claimed_at=timezone.now(),
    )
    with system_context(reason="test attempt refresh"):
        scheduled_step_run.refresh_from_db()

    assert attempt.ordinal == 1
    assert attempt.input_present is True
    assert attempt.input is None
    assert attempt.effect_key == scheduled_step_run.effect_key
    assert scheduled_step_run.current_attempt_id == attempt.pk
    assert scheduled_step_run.status == StepRunStatus.SCHEDULED


@pytest.mark.django_db(transaction=True)
def test_attempt_rows_and_owned_step_run_fields_reject_public_mutation(scheduled_step_run: StepRun) -> None:
    with system_context(reason="test attempt guard"):
        with pytest.raises(TypeError):
            StepAttempt.objects.create(
                step_run=scheduled_step_run,
                ordinal=1,
                cause="initial",
                lease_token=uuid.uuid4(),
                effect_key=uuid.uuid4(),
            )
        with pytest.raises(TypeError):
            StepAttempt._base_manager.bulk_create(
                [
                    StepAttempt(
                        step_run=scheduled_step_run,
                        ordinal=1,
                        cause="initial",
                        lease_token=uuid.uuid4(),
                        effect_key=uuid.uuid4(),
                    )
                ]
            )
    with pytest.raises(TypeError):
        StepRun.objects.filter(pk=scheduled_step_run.pk).update(effect_key=uuid.uuid4())

    scheduled_step_run.effect_key = uuid.uuid4()
    with pytest.raises(ValidationError, match="owned by StepAttemptManager"):
        scheduled_step_run.save()

    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    with system_context(reason="test attempt delete guard"):
        with pytest.raises(TypeError):
            StepAttempt._base_manager.filter(pk=attempt.pk).delete()


@pytest.mark.django_db(transaction=True)
def test_started_done_result_is_retained_and_projected_once(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    started_at = timezone.now()
    assert StepAttempt.objects.mark_started(attempt.pk, lease_token=attempt.lease_token, at=started_at)

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.DONE, output_present=True, output=None, outcome="ok"),
        recorded_at=timezone.now(),
    )
    repeated = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.DONE, output_present=True, output=None, outcome="ok"),
        recorded_at=timezone.now(),
    )
    with pytest.raises(ValidationError, match="different result"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.ERROR, error="must not replace"),
            recorded_at=timezone.now(),
        )
    with system_context(reason="test attempt refresh"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert finalized.recorded and finalized.applied
    assert not repeated.recorded and repeated.applied
    assert attempt.output_present and attempt.output is None and attempt.applied_at is not None
    assert scheduled_step_run.status == StepRunStatus.SUCCEEDED
    assert scheduled_step_run.outcome == "ok"


@pytest.mark.django_db(transaction=True)
def test_preparation_error_retains_unstarted_attempt_and_fails_scheduled_row(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    result = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="bad input"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="test attempt refresh"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert result.applied
    assert attempt.started_at is None
    assert scheduled_step_run.status == StepRunStatus.FAILED
    assert scheduled_step_run.error == "bad input"


@pytest.mark.django_db(transaction=True)
def test_revoked_lease_accepts_one_late_result_without_projection(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    revoked = StepAttempt.objects.revoke(
        attempt.pk,
        lease_token=attempt.lease_token,
        reason=LeaseRevocationReason.HEARTBEAT_LOST,
        at=timezone.now(),
    )
    late = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="late"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="test attempt refresh"):
        scheduled_step_run.refresh_from_db()

    assert revoked.revoked
    assert late.recorded and not late.applied
    assert scheduled_step_run.status == StepRunStatus.SCHEDULED

    repeated = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="late"),
        recorded_at=timezone.now(),
    )
    assert not repeated.recorded and not repeated.applied
    with pytest.raises(ValidationError, match="different result"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.ERROR, error="different"),
            recorded_at=timezone.now(),
        )


@pytest.mark.django_db(transaction=True)
def test_terminal_run_fences_lease_application_but_retains_result(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    with system_context(reason="test terminal attempt fence"):
        scheduled_step_run.run.mark_failed("stopped")
    result = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="late"),
        recorded_at=timezone.now(),
    )

    assert result.recorded and not result.applied


@pytest.mark.django_db(transaction=True)
def test_non_preparation_result_cannot_skip_started_transition(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    with pytest.raises(ValidationError, match="requires a started"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.DONE, output_present=True, output={"ok": True}),
            recorded_at=timezone.now(),
        )
    with system_context(reason="test attempt refresh"):
        attempt.refresh_from_db()
    assert attempt.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_stale_lease_token_cannot_record_a_result(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    result = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=uuid.uuid4(),
        result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="forged"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="test stale lease refresh"):
        attempt.refresh_from_db()

    assert not result.recorded and not result.applied
    assert attempt.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_out_of_order_heartbeat_never_moves_lease_freshness_backward(scheduled_step_run: StepRun) -> None:
    claimed_at = timezone.now()
    attempt = StepAttempt.objects.allocate(scheduled_step_run, claimed_at=claimed_at)
    newer = claimed_at + timedelta(seconds=10)
    assert StepAttempt.objects.mark_started(attempt.pk, lease_token=attempt.lease_token, at=claimed_at - timedelta(1))
    assert StepAttempt.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=newer)
    assert StepAttempt.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=claimed_at)
    with system_context(reason="test heartbeat freshness"):
        attempt.refresh_from_db()

    assert attempt.started_at == claimed_at
    assert attempt.heartbeat_at == newer


@pytest.mark.django_db(transaction=True)
def test_legacy_attempt_counter_allocates_next_without_fabricating_history(scheduled_step_run: StepRun) -> None:
    with system_context(reason="test legacy attempt counter"):
        models.QuerySet.update(StepRun.objects.filter(pk=scheduled_step_run.pk), attempt=3)
    with system_context(reason="test attempt refresh"):
        scheduled_step_run.refresh_from_db()
    attempt = StepAttempt.objects.allocate(scheduled_step_run)

    assert attempt.ordinal == 4
    with system_context(reason="test attempt history"):
        assert StepAttempt.objects.filter(step_run=scheduled_step_run).count() == 1

    with system_context(reason="test retained counter guard"):
        retained = StepRun.objects.get(pk=scheduled_step_run.pk)
        with pytest.raises(ValidationError, match="owned by StepAttemptManager"):
            retained.record_attempt()


@pytest.mark.django_db(transaction=True)
def test_user_deletion_nulls_only_attempt_attribution_and_retains_evidence(scheduled_step_run: StepRun) -> None:
    user = User.objects.create_user(username="retained-attempt-auditor")
    attempt = StepAttempt.objects.allocate(scheduled_step_run)
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="retained"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="test historical attempt attribution"):
        models.QuerySet.update(
            StepAttempt.objects.filter(pk=attempt.pk),
            created_by_id=user.pk,
            updated_by_id=user.pk,
        )
        before = StepAttempt.objects.values().get(pk=attempt.pk)

    with system_context(reason="test delete attributed user"):
        user.delete()

    with system_context(reason="test retained attempt after user deletion"):
        after = StepAttempt.objects.values().get(pk=attempt.pk)
    assert after["created_by_id"] is None and after["updated_by_id"] is None
    for field, value in before.items():
        if field not in {"created_by_id", "updated_by_id"}:
            assert after[field] == value
