"""Workflow transition hooks persist on the operation's selected writer."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from django.db import connection, connections, router, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import system_context

from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus, StepRunStatus, WorkflowStatus
from tests.test_transitions import TransitionRouter
from tests.workflows import StepRun, Workflow, WorkflowDispatch, WorkflowRun, start_run, workflow_with_steps


@pytest.fixture
def transition_writer(workflow_engine_tables: None) -> Iterator[str]:
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
    routing = TransitionRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="workflow status writer transition"):
        with CaptureQueriesContext(connection) as instance_queries:
            workflow.archive(using=transition_writer)
        stored = Workflow.objects.using(transition_writer).get(pk=workflow.pk)

    assert list(instance_queries) == []
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
    routing = TransitionRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])

    with system_context(reason="step projection writer transition"):
        with CaptureQueriesContext(connection) as instance_queries:
            step_run.mark_started(using=transition_writer, heartbeat_at=heartbeat_at, claimed_deliveries=3)
        stored = StepRun.objects.using(transition_writer).get(pk=step_run.pk)

    assert list(instance_queries) == []
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
    routing = TransitionRouter("unavailable-writer")
    monkeypatch.setattr(router, "routers", [routing])
    with system_context(reason="terminal writer transition"), transaction.atomic(using=transition_writer):
        with CaptureQueriesContext(connection) as instance_queries:
            getattr(run, transition_name)(using=transition_writer, **transition_kwargs)
        assert list(instance_queries) == []
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
