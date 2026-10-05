"""Root subject callbacks share native workflow admission and terminal commits."""

from types import SimpleNamespace

import pytest
from django.core.exceptions import ValidationError
from rebac import system_context

from angee.base.scoping import system_queryset
from angee.workflows import subjects
from angee.workflows.states import RunStatus, StepRunStatus
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import RunSubjectRecord, StepRecord, StepRun, WorkflowRun
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def subject(execution):
    with system_context(reason="tests.workflows.subject"):
        return RunSubjectRecord.objects.create()


def workflow_for(actor, *, step="echo"):
    return load_workflow(
        document("entry", step=step), key="subject-run", actor=actor,
        subject_model="workflows_testing.RunSubjectRecord",
    )


@pytest.mark.parametrize("missing", ["admit_run", "settle_run"])
def test_system_check_rejects_missing_hook(monkeypatch, missing):
    methods = {"admit_run": lambda self, run: None, "settle_run": lambda self, run, status: None}
    del methods[missing]
    model = type("IncompleteSubject", (subjects.RunSubject,), {
        "_meta": SimpleNamespace(label="example.IncompleteSubject"), **methods,
    })
    monkeypatch.setattr(subjects.apps, "get_models", lambda: [model])

    errors = subjects.check_run_subject_models()

    assert len(errors) == 1 and errors[0].id == "workflows.E002"
    assert missing in errors[0].msg


def test_request_replay_admits_once_and_settlement_reads_terminal_output(execution, subject):
    actor, _ = execution
    workflow = workflow_for(actor)
    run = WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"value": 4}, request_key="one")
    replay = WorkflowRun.objects.start(workflow, actor=actor, subject=subject, input={"value": 4}, request_key="one")
    assert replay.pk == run.pk
    run_until(run)
    WorkflowRun.objects.cancel(run, actor=actor)

    subject.refresh_from_db()
    assert subject.admissions == 1
    assert subject.settlements == [{"run": run.pk, "status": RunStatus.SUCCEEDED, "output": {"value": 4}}]


def test_admission_refusal_rolls_back_run_evidence_and_dispatch(execution, subject):
    actor, sent = execution
    workflow = workflow_for(actor)
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).update(refuse_admission=True)
    sent.clear()

    with pytest.raises(ValidationError, match="refuses admission"):
        WorkflowRun.objects.start(workflow, actor=actor, subject=subject)

    assert not system_queryset(WorkflowRun).exists()
    assert not system_queryset(StepRecord).exists()
    assert not system_queryset(StepRun).exists()
    assert sent == []


def test_cancel_settles_once_and_rolls_back_when_subject_raises(execution, subject):
    actor, _ = execution
    run = WorkflowRun.objects.start(workflow_for(actor), actor=actor, subject=subject)
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).update(refuse_settlement=True)

    with pytest.raises(ValidationError, match="refuses settlement"):
        WorkflowRun.objects.cancel(run, actor=actor)

    run.refresh_from_db()
    subject.refresh_from_db()
    assert run.status == RunStatus.RUNNING and subject.settlements == []
    assert system_queryset(StepRun).get(run=run).status == StepRunStatus.READY
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).update(refuse_settlement=False)
    assert WorkflowRun.objects.cancel(run, actor=actor).canceled
    assert not WorkflowRun.objects.cancel(run, actor=actor).canceled
    subject.refresh_from_db()
    assert subject.settlements == [{"run": run.pk, "status": RunStatus.CANCELED, "output": {}}]


def test_retry_readmits_and_refusal_keeps_run_and_step_failed(execution, subject):
    actor, _ = execution
    run = WorkflowRun.objects.start(workflow_for(actor, step="reject"), actor=actor, subject=subject)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).update(refuse_admission=True)

    with pytest.raises(ValidationError, match="refuses admission"):
        StepRun.objects.retry_step(step, actor=actor)

    run.refresh_from_db()
    step.refresh_from_db()
    assert run.status == RunStatus.FAILED and step.status == StepRunStatus.FAILED
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).update(refuse_admission=False)
    StepRun.objects.retry_step(step, actor=actor)
    run_until(run)
    subject.refresh_from_db()
    assert subject.admissions == 2
    assert subject.settlements == [{"run": run.pk, "status": RunStatus.FAILED, "output": {}}] * 2


def test_deleted_subject_stops_its_run(execution, subject):
    actor, sent = execution
    run = WorkflowRun.objects.start(workflow_for(actor), actor=actor, subject=subject)
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).delete()
    from angee.workflows.tasks import cancel

    payload = next(payload for name, payload in sent if name == "workflows.cancel")
    cancel(**payload["kwargs"])

    run.refresh_from_db()
    assert run.status == RunStatus.CANCELED
    assert run.stopped_at is not None
    assert not WorkflowRun.objects.cancel(run, actor=actor).canceled


def test_deleted_subject_prevents_reopen(execution, subject):
    actor, _ = execution
    run = WorkflowRun.objects.start(workflow_for(actor, step="reject"), actor=actor, subject=subject)
    run_until(run)
    step = system_queryset(StepRun).get(run=run)
    system_queryset(RunSubjectRecord).filter(pk=subject.pk).delete()

    with pytest.raises(ValidationError, match="cannot be retried|subject no longer exists"):
        StepRun.objects.retry_step(step, actor=actor)

    run.refresh_from_db()
    step.refresh_from_db()
    assert run.status == RunStatus.FAILED and step.status == StepRunStatus.FAILED


@pytest.mark.parametrize("relation", ["owned", "continuation"])
def test_child_runs_do_not_admit_or_settle_parent_subject(execution, subject, register_step, relation):
    actor, _ = execution
    child_workflow = workflow_for(actor)

    class StartChild(Echo):
        """Create a real child from the parent's fenced production attempt."""

        key = "subject_child"

        def run(self, ctx):
            ctx.start_run(child_workflow, subject=subject, input=ctx.input.model_dump(), relation=relation)
            return ctx.done(ctx.input)

    register_step(StartChild)
    parent_workflow = load_workflow(document("entry", step=StartChild.key), key="parent", actor=actor)
    parent = WorkflowRun.objects.start(parent_workflow, actor=actor)
    run_until(parent)
    child = system_queryset(WorkflowRun).get(parent_step__run=parent)
    if not child.is_terminal:
        run_until(child)
    subject.refresh_from_db()
    assert subject.admissions == 0 and subject.settlements == []
