"""Workflow-owned action blocks and restart interruption recovery."""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError
from rebac import generic_target, system_context

from angee.workflows.definition import Definition
from angee.workflows.runner import runner
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import StepRecord, StepRun, WorkflowRun
from tests.conftest import create_user
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


def blocked_document(model_label: str, action: str = "post", *, step: str = "echo") -> dict:
    """Declare one native test graph that blocks an action."""

    declaration = document("entry", step=step)
    declaration["blocks"] = [f"{model_label}:{action}"]
    return declaration


def test_definition_owns_canonical_block_contract() -> None:
    """Blocks retain one canonical model/action vocabulary in published documents."""

    definition = Definition.model_validate({"blocks": ["workflows.workflow:post"]})
    assert definition.blocks == ("workflows.Workflow:post",)

    with pytest.raises(PydanticValidationError, match="app.Model:action"):
        Definition.model_validate({"blocks": ["workflows.Workflow"]})
    with pytest.raises(PydanticValidationError, match="Invalid workflow block"):
        Definition.model_validate({"blocks": ["workflows.Workflow:Post"]})
    with pytest.raises(PydanticValidationError, match="must be unique"):
        Definition.model_validate({"blocks": ["workflows.Workflow:post", "workflows.workflow:post"]})


def test_blocking_finds_open_subject_and_recorded_runs(execution) -> None:
    """Pinned policy covers a run subject and every retained ``ctx.record`` target."""

    actor, _sent = execution
    other = create_user("workflow-block-other")
    model_label = actor._meta.label

    subject_workflow = load_workflow(
        blocked_document(model_label), key="blocks-subject", actor=actor, subject_model=model_label,
    )
    subject_run = WorkflowRun.objects.start(subject_workflow, actor=actor, subject=actor)

    recorded_workflow = load_workflow(
        blocked_document(model_label), key="blocks-record", actor=actor, subject_model=model_label,
    )
    recorded_run = WorkflowRun.objects.start(recorded_workflow, actor=actor, subject=other)
    target = generic_target(actor)
    with system_context(reason="workflow blocking retained-record fixture"):
        StepRecord.objects.create(
            run=recorded_run,
            step_run=StepRun.system_queryset().get(run=recorded_run),
            content_type=target.content_type,
            object_id=target.object_id,
        )

    unrelated_workflow = load_workflow(
        document("entry"), key="blocks-unrelated", actor=actor, subject_model=model_label,
    )
    unrelated_run = WorkflowRun.objects.start(unrelated_workflow, actor=actor, subject=actor)

    finished_workflow = load_workflow(
        blocked_document(model_label), key="blocks-finished", actor=actor, subject_model=model_label,
    )
    finished_run = WorkflowRun.objects.start(finished_workflow, actor=actor, subject=actor)
    run_until(finished_run)

    with system_context(reason="workflow blocking policy proof"):
        blocking = WorkflowRun.objects.blocking(actor, "post")
        assert set(blocking.values_list("pk", flat=True)) == {subject_run.pk, recorded_run.pk}
        assert not blocking.filter(pk=unrelated_run.pk).exists()
        assert not blocking.filter(pk=finished_run.pk).exists()
        assert not WorkflowRun.objects.blocking(actor, "read").exists()


def test_reap_interrupted_settles_only_running_runs(execution) -> None:
    """Restart recovery is terminal, closes open rows, and is idempotent."""

    actor, _sent = execution
    running_workflow = load_workflow(document("entry"), key="interrupted-running", actor=actor)
    running = WorkflowRun.objects.start(running_workflow, actor=actor)

    waiting_workflow = load_workflow(
        document("entry", step="pause"), key="interrupted-waiting", actor=actor,
    )
    waiting = WorkflowRun.objects.start(waiting_workflow, actor=actor)
    assert runner.execute(StepRun.system_queryset().get(run=waiting).pk)

    finished_workflow = load_workflow(document("entry"), key="interrupted-finished", actor=actor)
    finished = WorkflowRun.objects.start(finished_workflow, actor=actor)
    run_until(finished)

    assert WorkflowRun.objects.reap_interrupted() == 1
    running.refresh_from_db()
    waiting.refresh_from_db()
    finished.refresh_from_db()
    assert (running.status, running.outcome) == (RunStatus.INTERRUPTED, "interrupted")
    assert running.finished_at is not None and running.is_terminal
    assert StepRun.system_queryset().get(run=running).status == StepRunStatus.CANCELED
    assert waiting.status == RunStatus.WAITING
    assert StepRun.system_queryset().get(run=waiting).status == StepRunStatus.WAITING
    assert finished.status == RunStatus.SUCCEEDED
    assert WorkflowRun.objects.reap_interrupted() == 0
