"""Workflow transition persistence."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.testing.models import StepAttempt, StepRun, WorkflowDispatch, WorkflowRun
from angee.workflows.attempts import AttemptResult, AttemptResultKind
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus
from tests.workflows import start_run, workflow_with_steps


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("transition_name", "transition_kwargs", "expected_status", "expected_result"),
    [
        (
            "mark_succeeded",
            {"outcome": "done", "output": {"count": 3}},
            RunStatus.SUCCEEDED,
            {"status": "succeeded", "outcome": "done", "output": {"count": 3}, "error": None},
        ),
        (
            "mark_failed",
            {"error": "Writer failure"},
            RunStatus.FAILED,
            {"status": "failed", "outcome": "failed", "output": None, "error": "Writer failure"},
        ),
        (
            "mark_canceled",
            {},
            RunStatus.CANCELED,
            {"status": "canceled", "outcome": "canceled", "output": None, "error": None},
        ),
    ],
)
def test_terminal_transition_persists_and_publishes_once(
    composed_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    transition_name: str,
    transition_kwargs: dict[str, Any],
    expected_status: RunStatus,
    expected_result: dict[str, Any],
) -> None:
    del no_workflow_queue
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    run = start_run(workflow)
    with system_context(reason="terminal setup"):
        run.mark_running()
        run = WorkflowRun.objects.get(pk=run.pk)
    assert run._state.db == "default"
    with system_context(reason="terminal transition"), transaction.atomic():
        getattr(run, transition_name)(**transition_kwargs)
        stored = WorkflowRun.objects.get(pk=run.pk)
        assert stored.status == expected_status
        assert stored.result == expected_result
        assert stored.wake_at is None
        assert (
            WorkflowDispatch.objects.filter(
                kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk
            ).count()
            == 1
        )
        run.save(update_fields=["wake_at"])
        assert (
            WorkflowDispatch.objects.filter(
                kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk
            ).count()
            == 1
        )


@pytest.mark.django_db(transaction=True)
def test_partial_save_does_not_publish_unpersisted_terminal_status(
    composed_tables: None, no_workflow_queue: None
) -> None:
    del composed_tables, no_workflow_queue
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    run = start_run(workflow)
    with system_context(reason="terminal partial save"):
        names = [field.attname for field in WorkflowRun._meta.concrete_fields]
        stale = WorkflowRun.from_db(
            "default", names, [RunStatus.SUCCEEDED if name == "status" else getattr(run, name) for name in names]
        )
        stale.save(update_fields=["wake_at"])
        assert WorkflowRun.objects.get(pk=run.pk).status == RunStatus.PENDING
        assert not WorkflowDispatch.objects.filter(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk
        ).exists()


@pytest.mark.django_db(transaction=True)
def test_deferred_wake_preserves_checkpoint(
    composed_tables: None, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deferred wake preserves checkpoint."""
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    run = start_run(workflow)
    now = timezone.now()
    with system_context(reason="deferred wake fixture"):
        step_run = StepRun.objects.get(run=run)
    attempt = StepAttempt.objects.claim(step_run, claimed_at=now).attempt
    StepAttempt.objects.admit_invocation(attempt.pk, lease_token=attempt.lease_token, at=now)
    StepAttempt.objects.finalize(
        attempt.pk,
        lease_token=attempt.lease_token,
        result=AttemptResult(
            AttemptResultKind.WAIT,
            checkpoint_present=True,
            checkpoint={"cursor": 7},
            waiting_kind="external",
            requested_until=now + timedelta(hours=1),
        ),
        recorded_at=now,
    )
    with system_context(reason="deferred wake source"):
        step_run = StepRun.objects.only("pk").get(pk=step_run.pk)
    with system_context(reason="deferred wake write"):
        step_run.wake(at=now)
        stored = StepRun.objects.get(pk=step_run.pk)
    assert stored.status == StepRunStatus.WAITING
    assert stored.wait_until == now
    assert stored.resume_state == {"cursor": 7}
    assert stored.current_attempt_id == attempt.pk
