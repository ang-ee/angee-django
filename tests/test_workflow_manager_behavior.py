"""Workflow manager behavior."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import AttemptInput, AttemptResult, AttemptResultKind, WorkflowScope
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.states import RunOrigin, StepRunStatus
from tests.test_workflow_test_snapshots import _draft, _ReconcilingTestStep
from tests.test_workflows_triggers import _schedule_trigger
from tests.workflows import (
    Step,
    StepAttempt,
    StepRun,
    Trigger,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    admit_workflow_actor,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("node_scope", [False, True])
def test_start_test_reuses_snapshot_and_duplicate_request(
    workflow_engine_tables: None, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch, node_scope: bool
) -> None:
    """Start test reuses snapshot and duplicate request."""
    actor = get_user_model().objects.create_user(username="start-test")
    workflow, step = _draft(owner=actor)
    admit_workflow_actor(workflow, actor)
    snapshot = Workflow.objects.test_snapshot(workflow, expected_revision=workflow.draft_revision)
    owner = WorkflowRun.objects
    run = owner.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="same-snapshot",
        subject=None,
        actor=actor,
        scope=WorkflowScope.NODE if node_scope else WorkflowScope.WHOLE,
        selected_step=step if node_scope else None,
    )
    repeated = owner.start_test(
        workflow,
        expected_revision=workflow.draft_revision,
        request_key="same-snapshot",
        subject=None,
        actor=actor,
        scope=WorkflowScope.NODE if node_scope else WorkflowScope.WHOLE,
        selected_step=step if node_scope else None,
    )
    assert run.pk == repeated.pk
    assert run.workflow_id == snapshot.pk
    assert run.origin == RunOrigin.TEST
    with system_context(reason="manager test launch assertions"):
        assert StepRun.objects.filter(run_id=run.pk).count() == 1
        assert WorkflowDispatch.objects.filter(run_id=run.pk).count() == 1


def test_start_recovery_is_idempotent_and_persists_dispatch(
    workflow_engine_tables: None, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start recovery is idempotent and persists dispatch."""
    actor = get_user_model().objects.create_user(username="start-recovery")
    workflow, step = _draft(owner=actor)
    admit_workflow_actor(workflow, actor)
    with system_context(reason="manager recovery source fixture"):
        source_run = WorkflowRun.objects.create(workflow=workflow, status="running", created_by=actor)
        source_step = StepRun.objects.create(run=source_run, step=step, status=StepRunStatus.SCHEDULED)
    source = StepAttempt.objects.claim(source_step, input=AttemptInput(), claimed_at=timezone.now()).attempt
    StepAttempt.objects.admit_invocation(source.pk, lease_token=source.lease_token, at=timezone.now())
    StepAttempt.objects.finalize(
        source.pk,
        lease_token=source.lease_token,
        result=AttemptResult(AttemptResultKind.ERROR, error="retained failure"),
        recorded_at=timezone.now(),
    )
    monkeypatch.setattr(Step, "resolve_impl", lambda self, field: _ReconcilingTestStep)
    owner = WorkflowRun.objects
    recovery = owner.start_recovery(source, request_key="retry", actor=actor)
    repeated = owner.start_recovery(source, request_key="retry", actor=actor)
    assert recovery.pk == repeated.pk
    assert recovery.recovery_source_attempt_id == source.pk
    with system_context(reason="manager recovery assertions"):
        recovered_step = StepRun.objects.get(run_id=recovery.pk)
        dispatch = WorkflowDispatch.objects.get(run_id=recovery.pk)
    assert recovered_step.step_id == step.pk
    assert recovered_step.status == StepRunStatus.SCHEDULED
    assert dispatch.kind == WorkflowDispatchKind.ADVANCE


def test_schedule_maintenance_primes_claims_and_starts(
    workflow_engine_tables: None, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Schedule maintenance primes claims and starts."""
    now = timezone.now().replace(microsecond=0)
    primed = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=None)
    claimed = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=now)
    started = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=now)
    owner = Trigger.objects
    assert owner.prime_due_schedules(timestamp=now) == 1
    assert owner.claim_due_schedule(claimed.pk, timestamp=now) is not None
    admitted = owner.start_due_schedule(started.pk, timestamp=now)
    assert admitted is not None
    run, due_at = admitted
    assert due_at == now
    with system_context(reason="manager schedule assertions"):
        for trigger in (primed, claimed, started):
            trigger.refresh_from_db()
            assert trigger.next_fire_at == now + timedelta(seconds=60)
        assert WorkflowDispatch.objects.filter(run_id=run.pk).exists()
    assert claimed.hourly_fire_count == 1
    assert started.hourly_fire_count == 1
