"""Commit-bound cancellation delivery and permission preservation."""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import transaction
from rebac import to_subject_ref

from angee.base.scoping import system_queryset
from angee.jobs.enqueue import celery_app
from angee.workflows import tasks
from angee.workflows.managers import WorkflowRunManager
from angee.workflows.runner import runner
from angee.workflows.testing.drivers import load_workflow
from angee.workflows.testing.models import StepRun, WorkflowRun
from tests.conftest import create_user
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def target(execution):
    """Create a ready run, excluding its initial dispatch from observed delivery."""
    actor, sent = execution
    workflow = load_workflow(document("entry"), actor=actor)
    run = WorkflowRun.objects.start(workflow, actor=actor)
    sent.clear()
    return run


def cancellations(sent):
    """Select task payloads without simulating the transport or transition owner."""
    return [envelope["kwargs"] for name, envelope in sent if name == "workflows.cancel"]


def test_cancellation_waits_for_commit_and_duplicate_delivery_changes_nothing(target, execution):
    actor, sent = execution
    with transaction.atomic():
        WorkflowRun.objects.cancel_on_commit(target, actor)
        assert cancellations(sent) == []
        target.refresh_from_db()
        assert target.status == "running"
    assert cancellations(sent) == [{"run_id": target.pk, "actor": str(to_subject_ref(actor))}]
    tasks.cancel(**cancellations(sent)[0])
    target.refresh_from_db()
    assert target.status == "canceled"
    assert system_queryset(StepRun).get(run=target).status == "canceled"
    finished_at = target.finished_at
    tasks.cancel(**cancellations(sent)[0])
    target.refresh_from_db()
    assert target.finished_at == finished_at


@pytest.mark.parametrize("body_rollback", [False, True])
def test_context_cancellation_follows_the_body_transaction(target, execution, register_step, body_rollback):
    actor, sent = execution

    class CancelOther(Echo):
        key = "cancel_other"

        def run(self, ctx):
            ctx.cancel_run(ctx.load(WorkflowRun, target.sqid))
            assert cancellations(sent) == []
            return ctx.fail("Discard this body.") if body_rollback else ctx.done(ctx.input)

    register_step(CancelOther)
    workflow = load_workflow(document("entry", step=CancelOther.key), key="caller", actor=actor)
    caller = WorkflowRun.objects.start(workflow, actor=actor)
    assert runner.execute(system_queryset(StepRun).get(run=caller).pk)
    caller.refresh_from_db()
    assert caller.status == ("failed" if body_rollback else "succeeded")
    assert len(cancellations(sent)) == (0 if body_rollback else 1)
    target.refresh_from_db()
    assert target.status == "running"


def test_outer_rollback_discards_cancellation(target, execution):
    actor, sent = execution
    with transaction.atomic():
        WorkflowRun.objects.cancel_on_commit(target, actor)
        transaction.set_rollback(True)
    assert cancellations(sent) == []
    target.refresh_from_db()
    assert target.status == "running"


def test_cancellation_rechecks_the_original_requesters_permission(target, execution):
    actor, sent = execution
    viewer = create_user("cancel-viewer")
    target.with_actor(actor).grant_record_access("reader", viewer)
    with pytest.raises(PermissionDenied):
        WorkflowRun.objects.cancel_on_commit(target, viewer)
    assert cancellations(sent) == []

    target.with_actor(actor).grant_record_access("operator", viewer)
    WorkflowRun.objects.cancel_on_commit(target, viewer)
    assert cancellations(sent)[0]["actor"] == str(to_subject_ref(viewer))
    target.with_actor(actor).revoke_record_access("operator", viewer)
    with pytest.raises(PermissionDenied):
        tasks.cancel(**cancellations(sent)[0])
    target.refresh_from_db()
    assert target.status == "running"


def test_robust_delivery_failure_keeps_commit_and_later_callbacks(target, execution, monkeypatch, caplog):
    actor, _sent = execution
    observed = []

    def unavailable(*args, **kwargs):
        raise RuntimeError("Task transport unavailable")

    monkeypatch.setattr(celery_app, "send_task", unavailable)
    with transaction.atomic():
        WorkflowRun.objects.cancel_on_commit(target, actor)
        transaction.on_commit(lambda: observed.append("committed"))
    assert observed == ["committed"]
    assert "Task transport unavailable" in caplog.text


def test_missing_cancellation_target_is_an_idempotent_delivery(target, execution):
    actor, _sent = execution
    tasks.cancel(run_id=target.pk + 1, actor=str(to_subject_ref(actor)))


def test_worker_cancellation_waits_beyond_the_request_lock_budget(target, execution, monkeypatch):
    """A deferred task waits outside the caller lock instead of losing a busy target."""
    actor, _sent = execution
    timeouts = []
    cancel = WorkflowRunManager.cancel

    def observe(self, run, **kwargs):
        timeouts.append(kwargs["timeout"])
        return cancel(self, run, **kwargs)

    monkeypatch.setattr(WorkflowRunManager, "cancel", observe)
    tasks.cancel(run_id=target.pk, actor=str(to_subject_ref(actor)))
    assert timeouts == [None]
    target.refresh_from_db()
    assert target.status == "canceled"
