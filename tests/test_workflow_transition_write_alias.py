"""Workflow transition hooks persist on the operation's selected writer."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.db import connection, connections, router, transaction
from django.utils import timezone
from rebac import system_context

from angee.workflows.attempts import AttemptResult, AttemptResultKind
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus, WorkflowStatus
from tests.workflows import (
    StepAttempt,
    StepRun,
    Workflow,
    WorkflowDispatch,
    WorkflowRun,
    WorkflowWriteRouter,
    reject_default_domain_query,
    start_run,
    workflow_with_steps,
)
from tests.workflows import workflow_audit_frontier as workflow_audit_frontier
from tests.workflows import workflow_authorization_frontier as workflow_authorization_frontier


@pytest.fixture
def transition_writer(
    workflow_engine_tables: None,
    workflow_authorization_frontier: None,
    workflow_audit_frontier: list[dict[str, Any]],
) -> Iterator[str]:
    """Use a distinct connection to the fixture database for explicit writes."""

    del workflow_engine_tables
    alias = "writer"
    connections[alias] = connection.copy(alias=alias)
    try:
        yield alias
    finally:
        connections[alias].close()
        del connections[alias]


@pytest.mark.django_db(transaction=True)
def test_workflow_status_uses_explicit_writer_over_loaded_instance_alias(
    transition_writer: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    with system_context(reason="workflow status writer setup"):
        workflow = Workflow.objects.using("default").get(pk=workflow.pk)
    assert workflow._state.db == "default"
    routing = WorkflowWriteRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="workflow status writer transition"):
        with connection.execute_wrapper(reject_default_domain_query):
            workflow.archive(using=transition_writer)
        stored = Workflow.objects.using(transition_writer).get(pk=workflow.pk)

    assert routing.writes == []
    assert stored.status == WorkflowStatus.ARCHIVED


@pytest.mark.django_db(transaction=True)
def test_step_run_projection_uses_explicit_writer_over_loaded_instance_alias(
    transition_writer: str, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    del no_workflow_queue
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    run = start_run(workflow)
    with system_context(reason="step projection writer setup"):
        step_run = StepRun.objects.using("default").get(run_id=run.pk)
    assert step_run._state.db == "default"
    heartbeat_at = timezone.now()
    routing = WorkflowWriteRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="step projection writer transition"):
        with connection.execute_wrapper(reject_default_domain_query):
            step_run.mark_started(using=transition_writer, heartbeat_at=heartbeat_at, claimed_deliveries=3)
        stored = StepRun.objects.using(transition_writer).get(pk=step_run.pk)

    assert routing.writes == []
    assert stored.status == StepRunStatus.STARTED
    assert stored.heartbeat_at == heartbeat_at
    assert stored.claimed_deliveries == 3


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
def test_terminal_transition_persists_and_publishes_on_explicit_writer(
    transition_writer: str,
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
    with system_context(reason="terminal writer setup"):
        run.mark_running()
        run = WorkflowRun.objects.using("default").get(pk=run.pk)
    assert run._state.db == "default"
    routing = WorkflowWriteRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])
    with system_context(reason="terminal writer transition"), transaction.atomic(using=transition_writer):
        with connection.execute_wrapper(reject_default_domain_query):
            getattr(run, transition_name)(using=transition_writer, **transition_kwargs)
            assert routing.writes == []
        stored = WorkflowRun.objects.using(transition_writer).get(pk=run.pk)
        assert stored.status == expected_status
        assert stored.result == expected_result
        assert stored.wake_at is None
        assert (
            WorkflowDispatch.objects.using(transition_writer)
            .filter(kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk)
            .count()
            == 1
        )
        run.save(using=transition_writer, update_fields=["wake_at"])
        assert (
            WorkflowDispatch.objects.using(transition_writer)
            .filter(kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk)
            .count()
            == 1
        )


@pytest.mark.django_db(transaction=True)
def test_partial_save_does_not_publish_unpersisted_terminal_status(
    workflow_engine_tables: None, no_workflow_queue: None
) -> None:
    del workflow_engine_tables, no_workflow_queue
    workflow = workflow_with_steps(steps=({"key": "entry"},), edges=())
    run = start_run(workflow)
    with system_context(reason="terminal partial save"):
        names = [field.attname for field in WorkflowRun._meta.concrete_fields]
        stale = WorkflowRun.from_db(
            "default", names,
            [RunStatus.SUCCEEDED if name == "status" else getattr(run, name) for name in names],
        )
        stale.save(using="default", update_fields=["wake_at"])
        assert WorkflowRun.objects.using("default").get(pk=run.pk).status == RunStatus.PENDING
        assert (
            not WorkflowDispatch.objects.using("default")
            .filter(kind=WorkflowDispatchKind.ARTIFACT_DELIVERY, artifact_object_id=run.pk)
            .exists()
        )


@pytest.mark.django_db(transaction=True)
def test_deferred_wake_preserves_checkpoint_on_explicit_writer(
    transition_writer: str, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deferred status, ancestry, and checkpoint reload on the selected writer."""

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
    routing = WorkflowWriteRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="deferred wake write"), connection.execute_wrapper(reject_default_domain_query):
        step_run.wake(at=now, using=transition_writer)
        stored = StepRun.objects.using(transition_writer).get(pk=step_run.pk)

    assert routing.writes == []
    assert stored.status == StepRunStatus.WAITING
    assert stored.wait_until == now
    assert stored.resume_state == {"cursor": 7}
    assert stored.current_attempt_id == attempt.pk
