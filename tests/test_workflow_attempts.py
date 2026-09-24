"""Focused contracts for retained workflow execution attempts."""

from __future__ import annotations

import uuid
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, models
from django.db.models.signals import post_save, pre_save
from django.utils import timezone
from rebac import actor_context, system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.workflows import engine
from angee.workflows.attempts import (
    ArtifactSpec,
    AttemptCause,
    AttemptInput,
    AttemptResult,
    AttemptResultKind,
    DecisionSpec,
    DecisionTimerKind,
    GateResumeState,
    InvocationAdmission,
    JsonPresence,
    LeaseRevocationReason,
    RecoveryMode,
    deserialize_decision_specs,
    json_values_equal,
    serialize_decision_specs,
    validate_json_presence,
)
from angee.workflows.models import RunStatus, StepRunStatus
from angee.workflows.steps import GateStep, StepImpl, StepResult
from tests.workflows import (
    Decision,
    StepArtifact,
    StepAttempt,
    StepRun,
    WorkflowDispatch,
    WorkflowRun,
    workflow_with_steps,
)

User = get_user_model()


def _subject(username: str) -> str:
    """Create a real user and return its canonical REBAC subject."""

    user = User.objects.create_user(username=f"decision-{username}-{uuid.uuid4().hex}")
    return f"auth/user:{user.pk}"


def test_json_presence_rejects_coercive_or_nonfinite_values() -> None:
    assert validate_json_presence(JsonPresence(True, None)).value is None
    invalid = (
        JsonPresence(False, 0),
        JsonPresence(True, {1: "x"}),
        JsonPresence(True, ("x",)),
        JsonPresence(True, float("nan")),
    )
    for value in invalid:
        with pytest.raises(ValueError):
            validate_json_presence(value)


def test_json_equality_preserves_scalar_types_and_ignores_object_order() -> None:
    assert json_values_equal(
        {"a": [True, 1, 1.0], "b": None},
        {"b": None, "a": [True, 1, 1.0]},
    )
    assert not json_values_equal({"value": True}, {"value": 1})
    assert not json_values_equal({"value": 1}, {"value": 1.0})


@pytest.mark.django_db(transaction=True)
def test_duplicate_result_rejects_bool_number_substitution(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(
        scheduled_step_run, claimed_at=timezone.now()
    ).attempt
    StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=timezone.now()
    )
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE,
            output_present=True,
            output={"value": True},
        ),
        recorded_at=timezone.now(),
    )
    with pytest.raises(ValidationError, match="different result"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.DONE,
                output_present=True,
                output={"value": 1},
            ),
            recorded_at=timezone.now(),
        )


def test_attempt_result_rejects_non_json_output_and_checkpoint() -> None:
    for result in (
        AttemptResult(AttemptResultKind.DONE, output_present=True, output=("x",)),
        AttemptResult(
            AttemptResultKind.WAIT,
            checkpoint_present=True,
            checkpoint={"bad": float("nan")},
            requested_until=timezone.now(),
        ),
    ):
        with pytest.raises(ValidationError):
            StepAttempt.objects.validate_result(result)


def _set_retry_config(step_run: StepRun, retry: object) -> None:
    with system_context(reason="configure retained retry test"):
        models.QuerySet.update(
            type(step_run.step).objects.filter(pk=step_run.step_id),
            config={"retry": retry},
        )


def test_step_result_converts_to_retained_envelope_without_inventing_null_presence() -> None:
    until = timezone.now() + timedelta(minutes=5)
    decision = DecisionSpec(
        assignees=("auth/user:reviewer",),
        action="approve",
        expires_at=until,
    )

    waiting = StepResult.wait(until=until, resume_state=None).to_attempt_result()
    suspended = StepResult.suspend(decisions=(decision,), resume_state={"value": None}).to_attempt_result()

    assert waiting.kind == AttemptResultKind.WAIT
    assert waiting.requested_until == until
    assert not waiting.checkpoint_present
    assert suspended.kind == AttemptResultKind.SUSPEND
    assert suspended.checkpoint_present and suspended.checkpoint == {"value": None}
    assert suspended.decisions == (decision,)


def test_step_result_retains_explicit_ordered_artifact_associations() -> None:
    first, second = object(), object()
    envelope = StepResult.done(
        output=None,
        artifacts=(
            ArtifactSpec(target=first, label="First"),
            ArtifactSpec(target=second, label="Second"),
        ),
    ).to_attempt_result()

    assert envelope.artifacts_present
    assert envelope.artifacts == (
        ArtifactSpec(target=first, label="First"),
        ArtifactSpec(target=second, label="Second"),
    )
    waiting = StepResult.wait(
        until=timezone.now() + timedelta(minutes=1),
        artifacts=(ArtifactSpec(target=first, label="Pending owner record"),),
    ).to_attempt_result()
    assert waiting.artifacts_present
    assert waiting.artifacts == (ArtifactSpec(target=first, label="Pending owner record"),)


def test_step_result_failed_retains_error_checkpoint_and_artifacts() -> None:
    target = object()
    result = StepResult.failed(
        "Retained operation failure.",
        checkpoint={"evidence_id": "ext_test", "revision": 2},
        outcome="retained_failure",
        artifacts=(ArtifactSpec(target, "Failed evidence"),),
    ).to_attempt_result()

    assert result.kind == AttemptResultKind.ERROR
    assert result.error == "Retained operation failure."
    assert not result.output_present
    assert result.checkpoint_present
    assert result.checkpoint == {"evidence_id": "ext_test", "revision": 2}
    assert result.outcome == "retained_failure"
    assert result.artifacts == (ArtifactSpec(target, "Failed evidence"),)


def test_default_recovery_only_permits_explicit_fresh_replay() -> None:
    class ReplayableStep(StepImpl):
        def run(self, step_run: object, *, now: object) -> StepResult:
            del step_run, now
            return StepResult.done(outcome="replayed")

    implementation = ReplayableStep()
    result = implementation.run_recovery(
        object(), now=timezone.now(), source_attempt=object(), mode=RecoveryMode.FRESH
    )
    assert result.outcome == "replayed"
    with pytest.raises(ValidationError, match="does not implement reconciliation"):
        implementation.run_recovery(
            object(), now=timezone.now(), source_attempt=object(), mode=RecoveryMode.RECONCILE
        )


@pytest.mark.django_db(transaction=True)
def test_artifact_batch_is_retained_once_and_part_of_duplicate_result_identity(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    result = AttemptResult(
        AttemptResultKind.DONE,
        artifacts_present=True,
        artifacts=(ArtifactSpec(scheduled_step_run.run.workflow, "Workflow result"),),
    )
    first = StepAttempt.objects.finalize(
        attempt.pk, lease_token=attempt.lease_token, result=result, recorded_at=timezone.now()
    )
    duplicate = StepAttempt.objects.finalize(
        attempt.pk, lease_token=attempt.lease_token, result=result, recorded_at=timezone.now()
    )

    assert first.recorded and not duplicate.recorded
    with system_context(reason="inspect retained artifact"):
        artifact = StepArtifact.objects.get(attempt=attempt)
        queryset = StepArtifact.objects.filter(pk=artifact.pk)
        with pytest.raises(TypeError, match="immutable retained result evidence"):
            queryset._raw_delete(using=queryset.db)
    assert artifact.declaration_index == 0
    assert artifact.label == "Workflow result"
    with pytest.raises(ValidationError, match="different result"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.DONE,
                artifacts_present=True,
                artifacts=(ArtifactSpec(scheduled_step_run.run.workflow, "Changed"),),
            ),
            recorded_at=timezone.now(),
        )


@pytest.mark.django_db(transaction=True)
def test_artifact_history_groups_before_bound_without_widening_scope(
    scheduled_step_run: StepRun,
) -> None:
    """Repeated waits cannot crowd out a newer target or escape the supplied read scope."""

    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=timezone.now()
    )
    repeated = tuple(
        ArtifactSpec(scheduled_step_run.run.workflow, "Repeated output") for _ in range(201)
    )
    result = AttemptResult(
        AttemptResultKind.DONE,
        artifacts_present=True,
        artifacts=(*repeated,
                   ArtifactSpec(scheduled_step_run.run.workflow, "Distinct meaning"),
                   ArtifactSpec(scheduled_step_run.step, "Newer target"),
                   ArtifactSpec(scheduled_step_run.step, "Hidden by scope")),
    )
    StepAttempt.objects.finalize(
        attempt.pk, lease_token=attempt.lease_token, result=result, recorded_at=timezone.now()
    )

    with system_context(reason="inspect grouped workflow artifact history"):
        runs = WorkflowRun.objects.filter(pk=scheduled_step_run.run_id)
        scoped = StepArtifact.objects.exclude(label="Hidden by scope")
        bounded, truncated = scoped.history_page(runs, limit=2)
        bounded_rows = list(bounded.values_list("label", flat=True))
        complete, complete_truncated = scoped.history_page(runs, limit=3)
        complete_rows = list(complete.values_list("label", flat=True))

    assert truncated is True
    assert "Newer target" in bounded_rows
    assert "Hidden by scope" not in bounded_rows
    assert complete_truncated is False
    assert set(complete_rows) == {"Repeated output", "Distinct meaning", "Newer target"}


@pytest.mark.django_db(transaction=True)
def test_artifact_unique_index_rejects_reentrant_batch_insertion(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    target = scheduled_step_run.run.workflow
    content_type = ContentType.objects.get_for_model(target, for_concrete_model=False)

    def reenter(sender: object, instance: StepArtifact, **kwargs: object) -> None:
        del sender, kwargs
        StepArtifact.objects.retain_artifacts(
            instance.attempt,
            ((content_type.pk, target.pk, "Forged artifact"),),
        )

    post_save.connect(reenter, sender=StepArtifact, weak=False)
    try:
        with pytest.raises(IntegrityError):
            StepAttempt.objects.finalize(
                attempt.pk,
                lease_token=attempt.lease_token,
                result=AttemptResult(
                    AttemptResultKind.DONE,
                    artifacts_present=True,
                    artifacts=(ArtifactSpec(target, "Expected artifact"),),
                ),
                recorded_at=timezone.now(),
            )
    finally:
        post_save.disconnect(reenter, sender=StepArtifact)

    attempt.refresh_from_db()
    assert attempt.result_recorded_at is None
    with system_context(reason="inspect artifact signal rollback"):
        assert not StepArtifact.objects.filter(attempt=attempt).exists()


@pytest.mark.django_db(transaction=True)
def test_late_unapplied_result_still_retains_explicit_artifact(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=timezone.now()
    )
    StepAttempt.objects.cancel_current(scheduled_step_run.pk, at=timezone.now())
    finalization = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.DONE,
            artifacts_present=True,
            artifacts=(ArtifactSpec(scheduled_step_run.run.workflow, "Late artifact"),),
        ),
        recorded_at=timezone.now(),
    )

    assert finalization.recorded and not finalization.applied
    with system_context(reason="inspect late artifact"):
        assert StepArtifact.objects.filter(attempt=attempt, label="Late artifact").exists()


def test_gate_normalizes_legacy_naive_deadlines_before_retained_roundtrip() -> None:
    result = GateStep().run(
        SimpleNamespace(
            resume_state={},
            step=SimpleNamespace(
                config={
                    "action": "approve",
                    "slots": [{"assignee": "auth/user:reviewer"}],
                    "escalate_at": "2026-09-08T12:00:00",
                    "expires_at": "2026-09-08T13:00:00+00:00",
                }
            )
        ),
        now=timezone.now(),
    )
    declaration = result.decisions[0]

    assert timezone.is_aware(declaration.escalate_at)
    assert timezone.is_aware(declaration.expires_at)
    envelope = result.to_attempt_result()
    assert deserialize_decision_specs(serialize_decision_specs(envelope.decisions)) == envelope.decisions


def test_decision_declaration_rejects_unknown_constructor_fields() -> None:
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        DecisionSpec(
            assignees=("auth/user:reviewer",),
            action="approve",
            expires_att=timezone.now(),  # type: ignore[call-arg]
        )


@pytest.fixture()
def scheduled_step_run(workflow_engine_tables: None) -> StepRun:
    del workflow_engine_tables
    workflow = workflow_with_steps(steps=({"key": "start", "step_class": "agent_session"},), edges=())
    actor = User.objects.create_user(username=f"attempt-run-actor-{uuid.uuid4().hex}")
    with system_context(reason="test retained attempt setup"):
        run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            admitted_actor_ref=str(to_subject_ref(actor)),
            created_by=actor,
        )
        return StepRun.objects.create(run=run, step=workflow.steps.get(key="start"))


@pytest.mark.django_db(transaction=True)
def test_claim_initializes_stable_effect_identity_and_preserves_presence(scheduled_step_run: StepRun) -> None:
    claim = StepAttempt.objects.claim(
        scheduled_step_run,
        input=AttemptInput(present=True, value=None, provenance={"source": "constant"}),
        claimed_at=timezone.now(),
    )
    attempt = claim.attempt
    with system_context(reason="test attempt refresh"):
        scheduled_step_run.refresh_from_db()

    assert attempt.ordinal == 1
    assert attempt.input_present is True
    assert attempt.input is None
    assert attempt.effect_key == scheduled_step_run.effect_key
    assert scheduled_step_run.current_attempt_id == attempt.pk
    assert claim.newly_claimed
    assert scheduled_step_run.status == StepRunStatus.STARTED


@pytest.mark.django_db(transaction=True)
def test_database_command_receives_its_locked_attempt_input(
    scheduled_step_run: StepRun,
) -> None:
    admitted = AttemptInput(
        present=True,
        value={"document_id": "document-1"},
        provenance={"source": "map_item"},
    )
    attempt = StepAttempt.objects.claim(
        scheduled_step_run,
        input=admitted,
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(
        attempt.pk,
        lease_token=attempt.lease_token,
        at=timezone.now(),
    )

    def command(owned_step_run: StepRun, _owned_attempt: StepAttempt) -> AttemptResult:
        assert owned_step_run.pk == scheduled_step_run.pk
        assert _owned_attempt.input_present == admitted.present
        assert _owned_attempt.input == admitted.value
        assert _owned_attempt.input_provenance == admitted.provenance
        return AttemptResult(
            AttemptResultKind.DONE,
            output_present=True,
            output={"accepted": True},
        )

    finalized = StepAttempt.objects.execute_database_command(
        attempt.pk,
        lease_token=attempt.lease_token,
        command=command,
        recorded_at=timezone.now(),
    )
    assert finalized is not None and finalized.recorded and finalized.applied


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

    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    with system_context(reason="test attempt delete guard"):
        queryset = StepAttempt._base_manager.filter(pk=attempt.pk)
        with pytest.raises(TypeError, match="collection updates"):
            queryset.update(error="rewritten")
        with pytest.raises(TypeError, match="bulk_update"):
            queryset.bulk_update([attempt], ["error"])
        with pytest.raises(TypeError, match="retained execution evidence"):
            queryset.delete()
        with pytest.raises(TypeError, match="retained execution evidence"):
            queryset._raw_delete(using=queryset.db)


@pytest.mark.django_db(transaction=True)
def test_started_done_result_is_retained_and_projected_once(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    started_at = timezone.now()
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=started_at
    ) == InvocationAdmission.FIRST_START

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
        scheduled_step_run.run.refresh_from_db()

    assert finalized.recorded and finalized.applied
    assert not repeated.recorded and repeated.applied
    assert attempt.output_present and attempt.output is None and attempt.applied_at is not None
    assert scheduled_step_run.status == StepRunStatus.SUCCEEDED
    assert scheduled_step_run.outcome == "ok"


@pytest.mark.django_db(transaction=True)
def test_preparation_error_retains_unstarted_attempt_and_fails_scheduled_row(scheduled_step_run: StepRun) -> None:
    with pytest.raises(ValidationError, match="cannot carry output"):
        StepAttempt.objects.fail_preparation(
            scheduled_step_run,
            cause=AttemptCause.INITIAL,
            input=AttemptInput(),
            result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, output_present=True, output="lost"),
            claimed_at=timezone.now(),
            recorded_at=timezone.now(),
        )
    attempt = StepAttempt.objects.fail_preparation(
        scheduled_step_run,
        cause=AttemptCause.INITIAL,
        input=AttemptInput(),
        result=AttemptResult(AttemptResultKind.PREPARATION_ERROR, error="bad input"),
        claimed_at=timezone.now(),
        recorded_at=timezone.now(),
    )
    with system_context(reason="test attempt refresh"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert attempt.applied_at is not None
    assert attempt.started_at is None
    assert scheduled_step_run.status == StepRunStatus.FAILED
    assert scheduled_step_run.error == "bad input"
    assert scheduled_step_run.run.steps_taken == 1


@pytest.mark.django_db(transaction=True)
def test_revoked_lease_accepts_one_late_result_without_projection(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
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
    assert scheduled_step_run.status == StepRunStatus.STARTED

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
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    with system_context(reason="test terminal attempt fence"):
        scheduled_step_run.run.mark_failed("stopped")
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=timezone.now()
    ) == InvocationAdmission.FENCED
    result = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="late"),
        recorded_at=timezone.now(),
    )

    assert result.recorded and not result.applied


@pytest.mark.django_db(transaction=True)
def test_non_preparation_result_cannot_skip_started_transition(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
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
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
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
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=claimed_at).attempt
    newer = claimed_at + timedelta(seconds=10)
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=claimed_at - timedelta(1)
    ) == InvocationAdmission.FIRST_START
    assert StepAttempt.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=newer)
    assert StepAttempt.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=claimed_at)
    with system_context(reason="test heartbeat freshness"):
        attempt.refresh_from_db()

    assert attempt.started_at == claimed_at
    assert attempt.heartbeat_at == newer


@pytest.mark.django_db(transaction=True)
def test_duplicate_claim_reuses_immutable_input_and_does_not_advance_ordinal(scheduled_step_run: StepRun) -> None:
    claimed_at = timezone.now()
    attempt_input = AttemptInput(present=True, value={"value": None}, provenance={"source": "constant"})
    with system_context(reason="test claim delivery epoch"):
        models.QuerySet.update(WorkflowRun.objects.filter(pk=scheduled_step_run.run_id), deliveries=4)
    first = StepAttempt.objects.claim(
        scheduled_step_run,
        input=attempt_input,
        claimed_at=claimed_at,
    )
    duplicate = StepAttempt.objects.claim(
        scheduled_step_run,
        input=attempt_input,
        claimed_at=claimed_at + timedelta(seconds=1),
    )

    assert first.newly_claimed
    assert not duplicate.newly_claimed
    assert duplicate.attempt.pk == first.attempt.pk
    with system_context(reason="test duplicate claim history"):
        scheduled_step_run.refresh_from_db()
        scheduled_step_run.run.refresh_from_db()
        assert scheduled_step_run.attempt == 1
        assert scheduled_step_run.claimed_deliveries == 4
        assert scheduled_step_run.run.steps_taken == 1
        assert StepAttempt.objects.filter(step_run=scheduled_step_run).count() == 1

    with pytest.raises(ValidationError, match="different immutable input"):
        StepAttempt.objects.claim(
            scheduled_step_run,
            input=AttemptInput(present=True, value={"value": "different"}),
            claimed_at=claimed_at,
        )


@pytest.mark.django_db(transaction=True)
def test_first_physical_invocation_is_a_fenced_cas(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    at = timezone.now()

    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=uuid.uuid4(), at=at
    ) == InvocationAdmission.FENCED
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=at
    ) == InvocationAdmission.FIRST_START
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=at + timedelta(seconds=1)
    ) == InvocationAdmission.ALREADY_STARTED

    StepAttempt.objects.revoke(
        attempt.pk,
        lease_token=attempt.lease_token,
        reason=LeaseRevocationReason.CANCELED,
        at=at + timedelta(seconds=2),
    )
    assert StepAttempt.objects.admit_invocation(
        attempt.pk, lease_token=attempt.lease_token, at=at + timedelta(seconds=3)
    ) == InvocationAdmission.FENCED


@pytest.mark.django_db(transaction=True)
def test_waiting_rows_require_continuation_claims(scheduled_step_run: StepRun) -> None:
    with pytest.raises(ValidationError, match="requires an initial"):
        StepAttempt.objects.claim(
            scheduled_step_run,
            cause=AttemptCause.AUTOMATIC_RETRY,
            claimed_at=timezone.now(),
        )
    with system_context(reason="test continuation setup"):
        models.QuerySet.update(
            StepRun.objects.filter(pk=scheduled_step_run.pk),
            status=StepRunStatus.WAITING,
        )
        scheduled_step_run.refresh_from_db()

    with pytest.raises(ValidationError, match="requires a continuation"):
        StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now())
    claim = StepAttempt.objects.claim(
        scheduled_step_run,
        cause=AttemptCause.CONTINUATION,
        claimed_at=timezone.now(),
    )
    with system_context(reason="test continuation result"):
        scheduled_step_run.refresh_from_db()
    assert claim.newly_claimed
    assert scheduled_step_run.status == StepRunStatus.STARTED


@pytest.mark.django_db(transaction=True)
def test_terminal_run_rejects_claim(scheduled_step_run: StepRun) -> None:
    with system_context(reason="test terminal claim"):
        scheduled_step_run.run.mark_failed("stopped")
    with pytest.raises(ValidationError, match="terminal workflow run"):
        StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now())


@pytest.mark.django_db(transaction=True)
def test_attempt_counter_allocates_next_without_fabricating_history(scheduled_step_run: StepRun) -> None:
    with system_context(reason="test legacy attempt counter"):
        models.QuerySet.update(StepRun.objects.filter(pk=scheduled_step_run.pk), attempt=3)
    with system_context(reason="test attempt refresh"):
        scheduled_step_run.refresh_from_db()
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt

    assert attempt.ordinal == 4
    with system_context(reason="test attempt history"):
        assert StepAttempt.objects.filter(step_run=scheduled_step_run).count() == 1


@pytest.mark.django_db(transaction=True)
def test_user_deletion_nulls_only_attempt_attribution_and_retains_evidence(scheduled_step_run: StepRun) -> None:
    user = User.objects.create_user(username="retained-attempt-auditor")
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="retained"),
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


@pytest.mark.django_db(transaction=True)
def test_revoked_suspension_retains_exact_declarations_without_live_decisions(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.revoke(
        attempt.pk,
        lease_token=attempt.lease_token,
        reason=LeaseRevocationReason.CANCELED,
        at=timezone.now(),
    )
    expires_at = timezone.now() + timedelta(hours=1)
    reviewer = _subject("revoked-reviewer")
    spec = DecisionSpec(
        assignees=(reviewer,),
        action="approve",
        payload={"nullable": None},
        expires_at=expires_at,
    )

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.SUSPEND,
            checkpoint_present=True,
            checkpoint=None,
            decisions=(spec,),
            waiting_kind="approval",
        ),
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify late suspension evidence"):
        attempt.refresh_from_db()

    assert finalized.recorded and not finalized.applied and not finalized.timer_intents
    assert attempt.checkpoint_present and attempt.checkpoint is None
    assert deserialize_decision_specs(attempt.result_decisions)[0] == spec
    with system_context(reason="verify no late decisions"):
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 0


@pytest.mark.django_db(transaction=True)
def test_applicable_suspension_creates_ordered_decisions_rebac_and_timer_intents(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    escalate_at = timezone.now() + timedelta(minutes=5)
    expires_at = timezone.now() + timedelta(minutes=10)
    first = _subject("ordered-first")
    second = _subject("ordered-second")
    specs = (
        DecisionSpec(
            assignees=(first,),
            action="approve",
            priority=3,
            escalate_at=escalate_at,
        ),
            DecisionSpec(
                assignees=(second,),
                action="approve",
                priority=3,
                expires_at=expires_at,
                decision_schema={
                    "type": "object",
                    "required": ["action"],
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["complete"],
                            "options": [{
                                "value": "complete", "label": "Complete", "verdict": "COMPLETE",
                            }],
                        },
                        "document": {
                            "type": "object",
                            "widget": "object",
                            "properties": {
                                "counterparty": {"type": "string"},
                                "reference": {"type": "string"},
                                "lines": {
                                    "type": "array",
                                    "widget": "list",
                                    "items": {
                                        "type": "object",
                                        "widget": "object",
                                        "properties": {
                                            "description": {"type": "string"},
                                            "quantity": {"type": "number"},
                                        },
                                    },
                                },
                            },
                        },
                    },
                    "oneOf": [{
                        "type": "object",
                        "required": ["action"],
                        "properties": {
                            "action": {"const": "complete"},
                            "document": {
                                "type": "object",
                                "properties": {
                                    "counterparty": {"type": "string"},
                                    "reference": {"type": "string"},
                                    "lines": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "description": {"type": "string"},
                                                "quantity": {"type": "number"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        "additionalProperties": False,
                    }],
                },
            ),
    )

    retained_result = AttemptResult(
        AttemptResultKind.SUSPEND,
        checkpoint_present=True,
        checkpoint={"gate": True},
        decisions=specs,
        waiting_kind="approval",
    )
    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=retained_result,
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify suspension projection"):
        decisions = tuple(Decision.objects.filter(suspension_attempt=attempt).order_by("declaration_index"))
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert [decision.declaration_index for decision in decisions] == [0, 1]
    state = GateResumeState.from_checkpoint(scheduled_step_run.resume_state)
    assert state.decision_ids == [decision.pk for decision in decisions]
    retained_schema = state.decision_schemas[str(decisions[1].pk)]
    assert retained_schema["propertyOrder"] == ["action", "document"]
    document_schema = retained_schema["properties"]["document"]
    assert document_schema["propertyOrder"] == ["counterparty", "reference", "lines"]
    assert document_schema["properties"]["lines"]["items"]["propertyOrder"] == [
        "description",
        "quantity",
    ]
    assert deserialize_decision_specs(attempt.result_decisions) == specs
    assert [(intent.kind, intent.decision_id, intent.when) for intent in finalized.timer_intents] == [
        (DecisionTimerKind.ESCALATE, decisions[0].pk, escalate_at),
        (DecisionTimerKind.EXPIRE, decisions[1].pk, expires_at),
    ]
    relationship_model = active_relationship_model()
    relationship_count = relationship_model.objects.count()
    assert relationship_count >= 2

    repeated = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=retained_result,
        recorded_at=timezone.now(),
    )
    assert not repeated.recorded and repeated.applied
    assert repeated.timer_intents == finalized.timer_intents
    with system_context(reason="verify duplicate suspension"):
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 2
    assert relationship_model.objects.count() == relationship_count

    decisions[0].declaration_index = 4
    with pytest.raises(TypeError, match="immutable"):
        decisions[0].save()
    with pytest.raises(TypeError, match="owned by DecisionManager"):
        Decision.objects.filter(pk=decisions[0].pk).update(declaration_index=4)


@pytest.mark.django_db(transaction=True)
def test_suspension_issuer_shares_evidence_without_changing_decision_authority(
    scheduled_step_run: StepRun,
) -> None:
    """Self-review preserves issuer sharing without delegating it to reviewers."""

    issuer = scheduled_step_run.run.admission_actor()
    assert issuer is not None
    reviewer = User.objects.create_user(username="issuer-sharing-reviewer")
    reader = User.objects.create_user(username="issuer-sharing-reader")
    outsider = User.objects.create_user(username="issuer-sharing-outsider")
    issuer_ref = str(to_subject_ref(issuer))
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    with actor_context(outsider):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.SUSPEND,
                decisions=(
                    DecisionSpec(assignees=(issuer_ref,), action="self-review"),
                    DecisionSpec(
                        assignees=(str(to_subject_ref(reviewer)),),
                        action="independent-review",
                    ),
                    DecisionSpec(
                        assignees=(issuer_ref, str(to_subject_ref(reviewer))),
                        requester=issuer_ref,
                        action="requester-excluded",
                    ),
                ),
                waiting_kind="approval",
            ),
            recorded_at=timezone.now(),
        )
    with system_context(reason="test native Decision issuer authority"):
        self_review, independent, separated = tuple(
            Decision.objects.filter(suspension_attempt=attempt).order_by("declaration_index")
        )

    assert self_review.with_actor(issuer).has_access("act")
    assert self_review.with_actor(issuer).has_access("share")
    assert not self_review.with_actor(issuer).has_access("write")
    assert not self_review.with_actor(outsider).has_access("share")
    with actor_context(issuer):
        self_review.with_actor(issuer).grant_record_access("reader", reader)
    assert self_review.with_actor(reader).has_access("read")
    assert not self_review.with_actor(reader).has_access("act")
    assert not self_review.with_actor(reader).has_access("share")
    with actor_context(reader), pytest.raises(PermissionDenied):
        self_review.with_actor(reader).grant_record_access("reader", outsider)
    assert not self_review.with_actor(outsider).has_access("read")

    assert not independent.with_actor(issuer).has_access("act")
    assert independent.with_actor(issuer).has_access("read")
    assert independent.with_actor(issuer).has_access("share")
    assert independent.with_actor(reviewer).has_access("read")
    assert independent.with_actor(reviewer).has_access("act")
    assert not independent.with_actor(reviewer).has_access("share")
    with actor_context(reviewer), pytest.raises(PermissionDenied):
        independent.with_actor(reviewer).grant_record_access("reader", outsider)
    assert not independent.with_actor(outsider).has_access("read")
    assert not separated.with_actor(issuer).has_access("act")
    assert separated.with_actor(issuer).has_access("share")
    assert separated.with_actor(reviewer).has_access("act")
    with actor_context(issuer):
        self_review.with_actor(issuer).revoke_record_access("reader", reader)
    assert not self_review.with_actor(reader).has_access("read")


@pytest.mark.django_db(transaction=True)
def test_issuer_resync_uses_retained_admission_and_is_idempotent(
    scheduled_step_run: StepRun,
) -> None:
    """Old Decisions gain issuer sharing without trusting mutable audit users."""

    issuer = scheduled_step_run.run.admission_actor()
    assert issuer is not None
    outsider = User.objects.create_user(username="issuer-backfill-auditor")
    deleted = User.objects.create_user(username="issuer-backfill-deleted")
    deleted_subject = str(to_subject_ref(deleted))
    with system_context(reason="seed historical Decisions without issuer tuples"):
        deleted.delete()
        models.QuerySet.update(WorkflowRun.objects.filter(pk=scheduled_step_run.run_id), created_by=outsider)
        legacy = Decision.objects.create(step_run=scheduled_step_run, action="legacy", created_by=outsider)
        skipped = []
        for admission in ("", deleted_subject):
            run = WorkflowRun.objects.create(
                workflow=scheduled_step_run.run.workflow,
                status=RunStatus.RUNNING,
                admitted_actor_ref=admission,
                created_by=outsider,
            )
            step_run = StepRun.objects.create(run=run, step=scheduled_step_run.step)
            skipped.append(Decision.objects.create(step_run=step_run, action="legacy", created_by=outsider))

    assert not legacy.with_actor(issuer).has_access("share")
    command_output = StringIO()
    with actor_context(outsider):
        assert Decision.objects.resync_issuers() == 1
        call_command("resync_decision_issuers", stdout=command_output)
    assert "reconciled 1 Decision issuer(s)" in command_output.getvalue()
    relationships = active_relationship_model().objects.filter(resource_type="workflows/decision", relation="issuer")
    assert list(relationships.values_list("resource_id", "subject_id")) == [(str(legacy.pk), str(issuer.pk))]
    assert legacy.with_actor(issuer).has_access("share")
    assert legacy.with_actor(issuer).has_access("read")
    assert not legacy.with_actor(issuer).has_access("act")
    assert not legacy.with_actor(issuer).has_access("write")
    for decision in (legacy, *skipped):
        assert not decision.with_actor(outsider).has_access("share")
        assert not decision.with_actor(outsider).has_access("act")
        assert not decision.with_actor(outsider).has_access("write")


@pytest.mark.django_db(transaction=True)
def test_cancel_expires_applied_suspension_without_revoking_completed_attempt(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    reviewer = _subject("cancel-reviewer")
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.SUSPEND,
            decisions=(DecisionSpec(assignees=(reviewer,), action="approve"),),
            waiting_kind="approval",
        ),
        recorded_at=timezone.now(),
    )

    from angee.workflows import engine

    engine.cancel(scheduled_step_run.run, actor=scheduled_step_run.run.admission_actor())

    with system_context(reason="verify canceled retained suspension"):
        scheduled_step_run.refresh_from_db()
        attempt.refresh_from_db()
        decision = Decision.objects.get(suspension_attempt=attempt)
    assert scheduled_step_run.status == StepRunStatus.CANCELED
    assert attempt.applied_at is not None
    assert attempt.lease_revoked_at is None
    assert decision.verdict == "expired"
    assert decision.resolved_by == "workflows/cancel"


@pytest.mark.django_db(transaction=True)
def test_cancel_skips_decision_expiry_when_waiting_projection_has_failed_attempt(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="The draft changed."),
        recorded_at=timezone.now(),
    )
    scheduled_step_run.refresh_from_db()
    scheduled_step_run.status = StepRunStatus.WAITING
    with system_context(reason="retain waiting projection before run failure settles"):
        scheduled_step_run.project_from_attempt(attempt, fields={"status"})

    from angee.workflows import engine

    engine.cancel(scheduled_step_run.run, actor=scheduled_step_run.run.admission_actor())

    with system_context(reason="verify cancellation ignores failed waiting attempt"):
        scheduled_step_run.run.refresh_from_db()
        scheduled_step_run.refresh_from_db()
        attempt.refresh_from_db()
    assert scheduled_step_run.run.status == RunStatus.CANCELED
    assert scheduled_step_run.status == StepRunStatus.CANCELED
    assert attempt.result_kind == str(AttemptResultKind.ERROR)
    assert attempt.lease_revoked_at is None


@pytest.mark.django_db(transaction=True)
def test_decision_relationship_failure_rolls_back_entire_suspension(
    scheduled_step_run: StepRun, monkeypatch: pytest.MonkeyPatch
) -> None:
    from angee.workflows import managers as workflow_managers

    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    relationship_model = active_relationship_model()
    before_relationships = relationship_model.objects.count()
    native_write = workflow_managers.write_relationships
    calls = 0

    def fail_second_batch(relationships: object) -> None:
        nonlocal calls
        calls += 1
        native_write(relationships)
        if calls == 2:
            raise RuntimeError("relationship backend failed")

    monkeypatch.setattr(workflow_managers, "write_relationships", fail_second_batch)
    first = _subject("rollback-first")
    second = _subject("rollback-second")
    specs = (
        DecisionSpec(assignees=(first,), action="approve"),
        DecisionSpec(assignees=(second,), action="approve"),
    )

    with pytest.raises(RuntimeError, match="relationship backend failed"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.SUSPEND,
                decisions=specs,
                waiting_kind="approval",
            ),
            recorded_at=timezone.now(),
        )
    with system_context(reason="verify suspension rollback"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert attempt.result_recorded_at is None
    assert scheduled_step_run.status == StepRunStatus.STARTED
    with system_context(reason="verify no rolled-back decisions"):
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 0
    assert relationship_model.objects.count() == before_relationships


@pytest.mark.django_db(transaction=True)
def test_invalid_later_declaration_is_rejected_before_any_suspension_write(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    specs = (
        DecisionSpec(assignees=("auth/user:first",), action="approve"),
        DecisionSpec(assignees=("invalid-subject",), action="approve"),
    )

    with pytest.raises(ValidationError, match="Decision declarations"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(
                AttemptResultKind.SUSPEND,
                decisions=specs,
                waiting_kind="approval",
            ),
            recorded_at=timezone.now(),
        )
    with system_context(reason="verify invalid declaration batch"):
        attempt.refresh_from_db()
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 0
    assert attempt.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_retained_decision_creation_requires_current_suspension_and_rejects_bulk_bypasses(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    declaration = DecisionSpec(assignees=("auth/user:reviewer",), action="approve")

    with pytest.raises(ValidationError, match="current applicable suspension"):
        Decision.objects.create_for_suspension(
            step_run=scheduled_step_run,
            attempt=attempt,
            declarations=(declaration,),
        )
    with system_context(reason="test retained decision bulk guard"):
        with pytest.raises(TypeError, match="owned by DecisionManager"):
            Decision.objects.bulk_create(
                [
                    Decision(
                        step_run=scheduled_step_run,
                        suspension_attempt=attempt,
                        declaration_index=0,
                        action="approve",
                    )
                ]
            )
        legacy = Decision.objects.bulk_create(
            [Decision(step_run=scheduled_step_run, action="legacy")]
        )[0]
        legacy.action = "updated"
        with pytest.raises(TypeError, match="owned by DecisionManager"):
            Decision.objects.bulk_update([legacy], (name for name in ("action",)))
        legacy.save(update_fields=["action", "updated_at"])

    assert legacy.suspension_attempt_id is None
    assert legacy.declaration_index is None
    with system_context(reason="verify legacy decision bulk update"):
        assert Decision.objects.get(pk=legacy.pk).action == "updated"


@pytest.mark.parametrize("bypass", ("save", "update", "bulk_create"))
@pytest.mark.django_db(transaction=True)
def test_decision_generic_save_stays_closed_during_creation_signals(
    scheduled_step_run: StepRun,
    bypass: str,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    def attempt_bypass(**kwargs: object) -> None:
        instance = kwargs["instance"]
        assert isinstance(instance, Decision)
        if bypass == "save":
            Decision(
                step_run=scheduled_step_run,
                suspension_attempt=attempt,
                declaration_index=99,
                action="unrelated",
            ).save()
        elif bypass == "update":
            Decision.objects.filter(pk=instance.pk).update(declaration_index=99)
        else:
            Decision.objects.bulk_create(
                [
                    Decision(
                        step_run=scheduled_step_run,
                        suspension_attempt=attempt,
                        declaration_index=99,
                        action="unrelated",
                    )
                ]
            )

    post_save.connect(attempt_bypass, sender=Decision, weak=False)
    reviewer = _subject(f"signal-{bypass}")
    try:
        with pytest.raises(TypeError, match="Decision suspension provenance"):
            StepAttempt.objects.finalize(
                attempt.pk,
                lease_token=attempt.lease_token,
                result=AttemptResult(
                    AttemptResultKind.SUSPEND,
                    decisions=(DecisionSpec(assignees=(reviewer,), action="approve"),),
                    waiting_kind="approval",
                ),
                recorded_at=timezone.now(),
            )
    finally:
        post_save.disconnect(attempt_bypass, sender=Decision)

    with system_context(reason="verify signal bypass rollback"):
        attempt.refresh_from_db()
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 0
    assert attempt.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_non_json_later_declaration_is_rejected_before_any_suspension_write(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    specs = (
        DecisionSpec(assignees=("auth/user:first",), action="approve"),
        DecisionSpec.model_construct(
            assignees=("auth/user:second",),
            action="approve",
            payload={1: "coerced"},
            priority="3",
        ),
    )

    with pytest.raises(ValidationError, match="Decision declarations"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.SUSPEND, decisions=specs, waiting_kind="approval"),
            recorded_at=timezone.now(),
        )
    with system_context(reason="verify invalid typed declaration batch"):
        attempt.refresh_from_db()
        assert Decision.objects.filter(suspension_attempt=attempt).count() == 0
    assert attempt.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_returned_wait_deadline_is_retained_when_effective_wait_is_already_due(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    requested = timezone.now() + timedelta(days=1)
    recorded = timezone.now()
    with system_context(reason="deliver while invocation runs"):
        models.QuerySet.update(WorkflowRun.objects.filter(pk=scheduled_step_run.run_id), deliveries=1)

    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.WAIT,
            checkpoint_present=True,
            checkpoint=None,
            requested_until=requested,
            waiting_kind="scheduled",
        ),
        recorded_at=recorded,
    )
    with system_context(reason="verify requested and effective wait"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert attempt.result_requested_until == requested
    assert attempt.checkpoint_present and attempt.checkpoint is None
    assert scheduled_step_run.wait_until == recorded


@pytest.mark.django_db(transaction=True)
def test_transient_result_allocates_one_due_fenced_successor_without_logical_charge(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 3, "backoff": {"linear_wait": 10}})
    attempt_input = AttemptInput(present=True, value=None, provenance={"source": "constant"})
    attempt = StepAttempt.objects.claim(
        scheduled_step_run,
        input=attempt_input,
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    recorded_at = timezone.now()
    transient = AttemptResult(
        AttemptResultKind.TRANSIENT_ERROR,
        error="temporary",
        stacktrace="physical stack",
    )

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=transient,
        recorded_at=recorded_at,
    )
    assert finalized.recorded and finalized.applied and finalized.retry_intent is not None
    with system_context(reason="verify retained retry successor"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()
        scheduled_step_run.run.refresh_from_db()
        successor = StepAttempt.objects.get(pk=finalized.retry_intent.attempt_id)

    assert successor.retry_of_id == attempt.pk
    assert successor.retry_index == 1
    assert successor.ordinal == attempt.ordinal + 1 == scheduled_step_run.attempt
    assert successor.available_at == recorded_at + timedelta(seconds=10)
    assert successor.effect_key == attempt.effect_key
    assert successor.effect_generation == attempt.effect_generation
    assert successor.input_present and successor.input is None
    assert successor.input_provenance == attempt.input_provenance
    assert successor.lease_token != attempt.lease_token
    assert scheduled_step_run.current_attempt_id == successor.pk
    assert scheduled_step_run.status == StepRunStatus.STARTED
    assert scheduled_step_run.run.steps_taken == 1
    assert StepAttempt.objects.admit_invocation(
        successor.pk,
        lease_token=successor.lease_token,
        at=successor.available_at - timedelta(microseconds=1),
    ) == InvocationAdmission.NOT_DUE
    assert StepAttempt.objects.admit_invocation(
        successor.pk,
        lease_token=successor.lease_token,
        at=successor.available_at,
    ) == InvocationAdmission.FIRST_START

    StepAttempt.objects.finalize(
        successor.pk,
        lease_token=successor.lease_token,
        result=AttemptResult(AttemptResultKind.DONE, output_present=True, output={"ok": True}),
        recorded_at=successor.available_at,
    )
    repeated = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=transient,
        recorded_at=recorded_at + timedelta(minutes=1),
    )
    assert not repeated.recorded and repeated.applied
    assert repeated.retry_intent == finalized.retry_intent


@pytest.mark.django_db(transaction=True)
def test_exhausted_transient_result_projects_physical_failure_without_successor(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 1})
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.TRANSIENT_ERROR,
            error="still unavailable",
            stacktrace="physical stack",
        ),
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify exhausted retained retry"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert finalized.recorded and finalized.applied and finalized.retry_intent is None
    assert attempt.error == "still unavailable"
    assert attempt.stacktrace == "physical stack"
    assert attempt.orchestration_error == ""
    assert scheduled_step_run.status == StepRunStatus.FAILED
    assert scheduled_step_run.error == "still unavailable"
    with system_context(reason="verify no exhausted successor"):
        assert StepAttempt.objects.filter(retry_of=attempt).count() == 0


@pytest.mark.django_db(transaction=True)
def test_invalid_retry_policy_retains_physical_and_orchestration_errors_separately(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": "invalid"})
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="network failed"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify invalid retry policy"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert finalized.recorded and finalized.applied and finalized.retry_intent is None
    assert attempt.error == "network failed"
    assert "max_attempts" in attempt.orchestration_error
    assert scheduled_step_run.status == StepRunStatus.FAILED
    assert scheduled_step_run.error == "network failed"


@pytest.mark.django_db(transaction=True)
def test_retry_deadline_overflow_is_retained_as_expected_policy_failure(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(
        scheduled_step_run,
        {"max_attempts": 2, "backoff": {"exponential_wait": 10**100}},
    )
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="physical timeout"),
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify retry deadline overflow"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()

    assert finalized.recorded and finalized.applied and finalized.retry_intent is None
    assert attempt.error == "physical timeout"
    assert "Retry policy could not schedule a successor" in attempt.orchestration_error
    assert scheduled_step_run.status == StepRunStatus.FAILED
    with system_context(reason="verify no overflow retry successor"):
        assert StepAttempt.objects.filter(retry_of=attempt).count() == 0


@pytest.mark.django_db(transaction=True)
def test_unexpected_retry_allocation_failure_rolls_back_physical_result(
    scheduled_step_run: StepRun,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 2})
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    def fail_allocation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("storage failed")

    monkeypatch.setattr(type(StepAttempt.objects), "_allocate_retry_locked", fail_allocation)
    with pytest.raises(RuntimeError, match="storage failed"):
        StepAttempt.objects.finalize(
            attempt.pk,
            lease_token=attempt.lease_token,
            result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="physical error"),
            recorded_at=timezone.now(),
        )
    with system_context(reason="verify unexpected retry rollback"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()
    assert attempt.result_recorded_at is None
    assert attempt.error is None
    assert attempt.orchestration_error == ""
    assert scheduled_step_run.status == StepRunStatus.STARTED


@pytest.mark.parametrize("bypass", ("earlier_save", "unrelated_save", "collection_update"))
@pytest.mark.django_db(transaction=True)
def test_attempt_generic_save_stays_closed_during_result_signals(
    scheduled_step_run: StepRun,
    bypass: str,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 2})
    earlier = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(earlier.pk, lease_token=earlier.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        earlier.pk,
        lease_token=earlier.lease_token,
        result=AttemptResult(
            AttemptResultKind.WAIT,
            requested_until=timezone.now() + timedelta(minutes=1),
            waiting_kind="scheduled",
        ),
        recorded_at=timezone.now(),
    )
    current = StepAttempt.objects.claim(
        scheduled_step_run,
        cause=AttemptCause.CONTINUATION,
        claimed_at=timezone.now(),
    ).attempt
    StepAttempt.objects.admit_invocation(current.pk, lease_token=current.lease_token, at=timezone.now())

    other_workflow = workflow_with_steps(
        name="Other attempt owner",
        steps=({"key": "other", "step_class": "agent_session"},),
        edges=(),
    )
    with system_context(reason="create unrelated retained attempt"):
        other_run = WorkflowRun.objects.create(workflow=other_workflow, status=RunStatus.RUNNING)
        other_step_run = StepRun.objects.create(run=other_run, step=other_workflow.steps.get(key="other"))
    unrelated = StepAttempt.objects.claim(other_step_run, claimed_at=timezone.now()).attempt
    original_earlier_error = earlier.error
    original_unrelated_claimed_at = unrelated.claimed_at

    def attempt_bypass(**kwargs: object) -> None:
        if bypass == "earlier_save":
            earlier.error = "rewritten"
            earlier.save(update_fields=["error", "updated_at"])
        elif bypass == "unrelated_save":
            unrelated.claimed_at = timezone.now() + timedelta(days=1)
            unrelated.save(update_fields=["claimed_at", "updated_at"])
        else:
            StepAttempt.objects.filter(pk=earlier.pk).update(error="rewritten")

    post_save.connect(attempt_bypass, sender=StepAttempt, weak=False)
    try:
        with pytest.raises(TypeError, match="Step attempts"):
            StepAttempt.objects.finalize(
                current.pk,
                lease_token=current.lease_token,
                result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="retry me"),
                recorded_at=timezone.now(),
            )
    finally:
        post_save.disconnect(attempt_bypass, sender=StepAttempt)

    with system_context(reason="verify attempt signal bypass rollback"):
        earlier.refresh_from_db()
        unrelated.refresh_from_db()
        current.refresh_from_db()
        assert StepAttempt.objects.filter(retry_of=current).count() == 0
    assert earlier.error == original_earlier_error
    assert unrelated.claimed_at == original_unrelated_claimed_at
    assert current.result_recorded_at is None


@pytest.mark.django_db(transaction=True)
def test_retained_step_run_projection_and_ancestry_reject_public_writes(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    original_run_id = scheduled_step_run.run_id
    original_step_id = scheduled_step_run.step_id

    with pytest.raises(TypeError, match="StepAttemptManager"):
        StepRun.objects.filter(pk=scheduled_step_run.pk).update(status=StepRunStatus.FAILED)
    with pytest.raises(TypeError, match="StepAttemptManager"):
        StepRun.objects.bulk_update([scheduled_step_run], (name for name in ("output",)))
    with pytest.raises(TypeError, match="StepAttemptManager"):
        StepRun.objects.filter(pk=scheduled_step_run.pk).update(map_index=3)

    scheduled_step_run.run_id = original_run_id + 1
    with pytest.raises(ValidationError, match="ancestry"):
        scheduled_step_run.save(update_fields=["run", "updated_at"])

    with system_context(reason="verify retained StepRun ancestry"):
        scheduled_step_run.refresh_from_db()
        attempt.refresh_from_db()
    assert scheduled_step_run.run_id == original_run_id
    assert scheduled_step_run.step_id == original_step_id
    assert scheduled_step_run.current_attempt_id == attempt.pk


@pytest.mark.django_db(transaction=True)
def test_step_run_generic_save_stays_closed_during_projection_signals(
    scheduled_step_run: StepRun,
) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())

    def rewrite_projection(sender: object, instance: StepRun, **kwargs: object) -> None:
        del sender, kwargs
        instance.output = {"forged": True}
        instance.save(update_fields=["output", "updated_at"])

    post_save.connect(rewrite_projection, sender=StepRun, weak=False)
    try:
        with pytest.raises(TypeError, match="manager owner"):
            StepAttempt.objects.finalize(
                attempt.pk,
                lease_token=attempt.lease_token,
                result=AttemptResult(AttemptResultKind.DONE, output_present=True, output={"ok": True}),
                recorded_at=timezone.now(),
            )
    finally:
        post_save.disconnect(rewrite_projection, sender=StepRun)

    with system_context(reason="verify StepRun signal rollback"):
        attempt.refresh_from_db()
        scheduled_step_run.refresh_from_db()
    assert attempt.result_recorded_at is None
    assert scheduled_step_run.status == StepRunStatus.STARTED
    assert scheduled_step_run.output == {}


@pytest.mark.django_db(transaction=True)
def test_revoked_transient_result_is_late_evidence_without_successor(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 3})
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    StepAttempt.objects.revoke(
        attempt.pk,
        lease_token=attempt.lease_token,
        reason=LeaseRevocationReason.HEARTBEAT_LOST,
        at=timezone.now(),
    )

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="late transient"),
        recorded_at=timezone.now(),
    )

    assert finalized.recorded and not finalized.applied and finalized.retry_intent is None
    with system_context(reason="verify no late transient successor"):
        assert StepAttempt.objects.filter(retry_of=attempt).count() == 0


@pytest.mark.django_db(transaction=True)
def test_continuation_starts_a_fresh_retry_series_without_charging_automatic_successor(
    scheduled_step_run: StepRun,
) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 2})
    first = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(first.pk, lease_token=first.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        first.pk,
        lease_token=first.lease_token,
        result=AttemptResult(
            AttemptResultKind.WAIT,
            requested_until=timezone.now() + timedelta(minutes=1),
            waiting_kind="scheduled",
        ),
        recorded_at=timezone.now(),
    )
    with system_context(reason="refresh continuation source"):
        scheduled_step_run.refresh_from_db()
    continuation = StepAttempt.objects.claim(
        scheduled_step_run,
        cause=AttemptCause.CONTINUATION,
        claimed_at=timezone.now(),
    ).attempt
    assert continuation.retry_index == 0 and continuation.retry_of_id is None
    StepAttempt.objects.admit_invocation(
        continuation.pk,
        lease_token=continuation.lease_token,
        at=timezone.now(),
    )

    finalized = StepAttempt.objects.finalize(
        continuation.pk,
        lease_token=continuation.lease_token,
        result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="retry continuation"),
        recorded_at=timezone.now(),
    )
    assert finalized.retry_intent is not None
    with system_context(reason="verify continuation retry series"):
        successor = StepAttempt.objects.get(pk=finalized.retry_intent.attempt_id)
        scheduled_step_run.run.refresh_from_db()
    assert successor.retry_index == 1
    assert successor.retry_of_id == continuation.pk
    assert scheduled_step_run.run.steps_taken == 2


@pytest.mark.django_db(transaction=True)
def test_terminal_run_retains_transient_result_without_successor(scheduled_step_run: StepRun) -> None:
    _set_retry_config(scheduled_step_run, {"max_attempts": 3})
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    with system_context(reason="terminal retained retry fence"):
        scheduled_step_run.run.mark_failed("stopped")

    finalized = StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.TRANSIENT_ERROR, error="after terminal"),
        recorded_at=timezone.now(),
    )

    assert finalized.recorded and not finalized.applied and finalized.retry_intent is None
    with system_context(reason="verify no terminal transient successor"):
        assert StepAttempt.objects.filter(retry_of=attempt).count() == 0


@pytest.mark.django_db(transaction=True)
def test_invocation_admission_is_a_write_once_conditional_update(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    at = timezone.now()
    with system_context(reason="verify conditional invocation admission"):
        assert not attempt.admit_invocation(lease_token=uuid.uuid4(), at=at)
        assert attempt.admit_invocation(lease_token=attempt.lease_token, at=at)
        assert not attempt.admit_invocation(
            lease_token=attempt.lease_token, at=at + timedelta(seconds=1)
        )
        attempt.refresh_from_db()
    assert attempt.started_at == at
    assert attempt.heartbeat_at == at


@pytest.mark.django_db(transaction=True)
def test_settlement_conditional_update_rejects_duplicate_and_non_suspension(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    attempt.decision_settlement = {"decision_ids": [1], "outcome": "approved"}
    with system_context(reason="verify non-suspension cannot settle"):
        with pytest.raises(ValidationError, match="not available for settlement"):
            attempt.settle_decisions()
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(AttemptResultKind.SUSPEND, checkpoint_present=True, checkpoint={}),
        recorded_at=timezone.now(),
    )
    with system_context(reason="verify conditional suspension settlement"):
        attempt.refresh_from_db()
        attempt.decision_settlement = {"decision_ids": [1], "outcome": "approved"}
        attempt.settle_decisions()
        with pytest.raises(ValidationError, match="not available for settlement"):
            attempt.settle_decisions()
        attempt.refresh_from_db()
    assert attempt.decision_settlement == {"decision_ids": [1], "outcome": "approved"}


@pytest.mark.django_db(transaction=True)
def test_pre_save_cannot_reenter_finalization_before_result_is_visible(scheduled_step_run: StepRun) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    result = AttemptResult(AttemptResultKind.DONE, output_present=True, output={"ok": True})
    reentries = []

    def reenter(sender: object, instance: StepAttempt, **kwargs: object) -> None:
        del sender, kwargs
        if instance.pk == attempt.pk:
            reentries.append(
                StepAttempt.objects.finalize(
                    attempt.pk, lease_token=attempt.lease_token, result=result, recorded_at=timezone.now()
                )
            )

    pre_save.connect(reenter, sender=StepAttempt, weak=False)
    try:
        finalization = StepAttempt.objects.finalize(
            attempt.pk, lease_token=attempt.lease_token, result=result, recorded_at=timezone.now()
        )
    finally:
        pre_save.disconnect(reenter, sender=StepAttempt)
    assert finalization.recorded and finalization.applied
    assert len(reentries) == 1 and not reentries[0].recorded


@pytest.mark.django_db(transaction=True)
def test_start_checks_the_pinned_actor_inside_system_context(scheduled_step_run: StepRun) -> None:
    stranger = User.objects.create_user(username="workflow-start-denied")
    with system_context(reason="verify start cannot inherit ambient system permission"):
        before = WorkflowRun.objects.count()
        with pytest.raises(PermissionDenied, match="cannot start"):
            WorkflowRun.objects.start(scheduled_step_run.run.workflow, None, stranger)
        with pytest.raises(PermissionDenied, match="explicit actor"):
            WorkflowRun.objects.start(scheduled_step_run.run.workflow, None, None)
        assert WorkflowRun.objects.count() == before


@pytest.mark.parametrize("operation", ("heartbeat", "revoke"))
@pytest.mark.django_db(transaction=True)
def test_lease_operations_are_visible_to_pre_save_reentry(scheduled_step_run: StepRun, operation: str) -> None:
    attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    at = timezone.now() + timedelta(seconds=1)
    reentries = []

    def invoke() -> object:
        if operation == "heartbeat":
            return StepAttempt.objects.heartbeat(attempt.pk, lease_token=attempt.lease_token, at=at)
        return StepAttempt.objects.revoke(
            attempt.pk,
            lease_token=attempt.lease_token,
            reason=LeaseRevocationReason.CANCELED,
            at=at,
        )

    def reenter(sender: object, instance: StepAttempt, **kwargs: object) -> None:
        del sender, kwargs
        if instance.pk == attempt.pk:
            reentries.append(invoke())

    pre_save.connect(reenter, sender=StepAttempt, weak=False)
    try:
        invoke()
    finally:
        pre_save.disconnect(reenter, sender=StepAttempt)
    assert len(reentries) == 1
    with system_context(reason="verify committed lease operation"):
        attempt.refresh_from_db()
    if operation == "heartbeat":
        assert attempt.heartbeat_at == at
    else:
        assert attempt.lease_revoked_at == at


@pytest.mark.parametrize("relationship", ("child", "recovery"))
@pytest.mark.parametrize("entry_point", ("command", "dispatch"))
@pytest.mark.django_db(transaction=True)
@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL execution ancestry row locks")
def test_database_command_locks_execution_ancestors_before_consumer_rows(
    scheduled_step_run: StepRun,
    monkeypatch: pytest.MonkeyPatch,
    relationship: str,
    entry_point: str,
) -> None:
    source_run = scheduled_step_run.run
    actor_ref = source_run.admitted_actor_ref
    if relationship == "recovery":
        source_attempt = StepAttempt.objects.claim(scheduled_step_run, claimed_at=timezone.now()).attempt
        StepAttempt.objects.admit_invocation(
            source_attempt.pk, lease_token=source_attempt.lease_token, at=timezone.now()
        )
        StepAttempt.objects.finalize(
            source_attempt.pk,
            lease_token=source_attempt.lease_token,
            result=AttemptResult(AttemptResultKind.ERROR, error="Retained recovery source"),
            recorded_at=timezone.now(),
        )
        ancestry = {
            "origin": "recovery",
            "recovery_source_attempt": source_attempt,
            "recovery_request_actor_ref": actor_ref,
            "recovery_mode": RecoveryMode.FRESH,
        }
        cause = AttemptCause.MANUAL_RETRY
    else:
        ancestry = {
            "origin": "workflow",
            "parent_step_run": scheduled_step_run,
            "parent_relation": "continuation",
        }
        cause = AttemptCause.INITIAL
    with system_context(reason="execution lock-order fixture"):
        consumer_run = WorkflowRun.objects.create(
            workflow=source_run.workflow,
            status=RunStatus.RUNNING,
            admitted_actor_ref=actor_ref,
            created_by=source_run.created_by,
            **ancestry,
        )
        consumer = StepRun.objects.create(run=consumer_run, step=scheduled_step_run.step)
    attempt = StepAttempt.objects.claim(consumer, cause=cause, claimed_at=timezone.now()).attempt
    dispatch, _ = WorkflowDispatch.objects.schedule_execute(attempt)
    if entry_point != "dispatch":
        StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=timezone.now())
    events: list[tuple[str, tuple[Any, ...]]] = []

    def observe(execute: Any, sql: str, params: Any, many: bool, context: Any) -> Any:
        if "FOR UPDATE" in sql:
            for model in (WorkflowRun, StepRun, StepAttempt):
                if f'FROM "{model._meta.db_table}"' in sql:
                    events.append((model.__name__, tuple(params)))
                    break
        return execute(sql, params, many, context)

    def command() -> Any:
        return StepAttempt.objects.execute_database_command(
            attempt.pk,
            lease_token=attempt.lease_token,
            command=lambda step_run, owned_attempt: AttemptResult(AttemptResultKind.DONE),
            recorded_at=timezone.now(),
        )

    with connection.execute_wrapper(observe):
        if entry_point == "dispatch":
            finalizations = []

            def execute_attempt(*args: Any, **kwargs: Any) -> dict[str, int]:
                finalizations.append(command())
                return {"executed": 1}

            monkeypatch.setattr(engine, "execute_attempt", execute_attempt)
            WorkflowDispatch.objects.deliver(
                dispatch.pk,
                expected_target_id=attempt.pk,
                lease_token=attempt.lease_token,
                now=timezone.now(),
            )
            finalization = finalizations[0]
        else:
            finalization = command()
    assert finalization is not None and finalization.recorded and finalization.applied
    assert events[:4] == [
        ("WorkflowRun", (source_run.pk,)),
        ("WorkflowRun", (consumer_run.pk,)),
        ("StepRun", (consumer.pk,)),
        ("StepAttempt", (attempt.pk,)),
    ]
