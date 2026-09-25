"""Cancellation admission and durable-intent ownership."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from rebac import system_context, to_subject_ref

from angee.testing.models import Step, StepRun, Workflow, WorkflowDispatch, WorkflowRun
from angee.workflows import engine
from angee.workflows.dispatch import WorkflowDispatchKind
from angee.workflows.models import RunStatus


@pytest.fixture
def cancelable_run(composed_tables: None, no_workflow_queue: None) -> tuple[Any, Any]:
    del composed_tables, no_workflow_queue
    actor = get_user_model().objects.create_user(username="cancellation-owner")
    with system_context(reason="test cancellation admission setup"):
        workflow = Workflow.objects.create(name="Cancelable")
        run = WorkflowRun.objects.create(
            workflow=workflow,
            status=RunStatus.RUNNING,
            created_by=actor,
            admitted_actor_ref=str(to_subject_ref(actor)),
        )
    return run, actor


def test_cancel_rejects_an_unprivileged_actor_inside_system_scope(cancelable_run: tuple[Any, Any]) -> None:
    run, _owner = cancelable_run
    stranger = get_user_model().objects.create_user(username="cancellation-stranger")
    with system_context(reason="test cancellation cannot inherit elevation"):
        with pytest.raises(PermissionDenied):
            WorkflowRun.objects.cancel(run, actor=stranger)
        run.refresh_from_db()
        assert run.status == RunStatus.RUNNING
        assert not WorkflowDispatch.objects.filter(run=run).exists()


def test_cancel_requires_an_actor_and_reuses_the_terminal_result(cancelable_run: tuple[Any, Any]) -> None:
    run, owner = cancelable_run
    with system_context(reason="test cancellation requires explicit admission"):
        with pytest.raises(PermissionDenied):
            engine.cancel(run, actor=None)
    engine.cancel(run, actor=owner)
    engine.cancel(run, actor=owner)
    with system_context(reason="inspect canceled run"):
        run.refresh_from_db()
        delivery_count = WorkflowDispatch.objects.filter(
            kind=WorkflowDispatchKind.ARTIFACT_DELIVERY,
            artifact_object_id=run.pk,
        ).count()
    assert run.status == RunStatus.CANCELED
    assert delivery_count == 1


def test_child_cancel_requires_its_persisted_parent_to_be_terminal(cancelable_run: tuple[Any, Any]) -> None:
    parent, _owner = cancelable_run
    with system_context(reason="test retained premature child intent"), transaction.atomic():
        step = Step.objects.create(workflow=parent.workflow, key="call", name="Call", step_class="fixture")
        slot = StepRun.objects.create(run=parent, step=step)
        child = WorkflowRun.objects.create(
            workflow=parent.workflow,
            parent_step_run=slot,
            parent_relation="owned_call",
            status=RunStatus.RUNNING,
        )
        intent, _created = WorkflowDispatch.objects.schedule_child_cancel(child)
    with pytest.raises(ValidationError, match="terminal parent"):
        WorkflowDispatch.objects.deliver(intent.pk, expected_target_id=child.pk)
    with system_context(reason="inspect rejected child cancellation"):
        intent.refresh_from_db()
        child.refresh_from_db()
    assert intent.consumed_at is None
    assert child.status == RunStatus.RUNNING
