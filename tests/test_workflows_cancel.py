"""Commit-bound cancellation delivery and permission preservation."""

import pytest
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from rebac import to_subject_ref

from angee.jobs.enqueue import celery_app
from angee.workflows import tasks
from angee.workflows.managers import WorkflowRunManager
from angee.workflows.runner import runner
from angee.workflows.signals import isolated_run_cleanup, run_stopping
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


def test_deferred_cancellation_cleans_up_once_when_the_task_transitions(target, execution):
    actor, sent = execution
    observed = []

    @isolated_run_cleanup
    def stopping(sender, *, run, **kwargs):
        observed.append(run.pk)

    run_stopping.connect(stopping)
    try:
        with transaction.atomic():
            WorkflowRun.objects.cancel_on_commit(target, actor)
            assert observed == [] and cancellations(sent) == []
            transaction.set_rollback(True)
        assert observed == [] and cancellations(sent) == []
        with transaction.atomic():
            WorkflowRun.objects.cancel_on_commit(target, actor)
            assert observed == [] and cancellations(sent) == []
        assert observed == []
        tasks.cancel(**cancellations(sent)[0])
        tasks.cancel(**cancellations(sent)[0])
        assert observed == [target.pk]
        target.refresh_from_db()
        assert target.status == "canceled"
    finally:
        run_stopping.disconnect(stopping)


@pytest.mark.parametrize("failure", [ValueError, IntegrityError])
def test_cleanup_receiver_failure_rolls_back_only_its_writes_and_finishes_the_run(
    target, execution, caplog, failure,
):
    actor, _sent = execution
    observed = []

    @isolated_run_cleanup
    def failing(sender, *, run, **kwargs):
        observed.append("failing")
        WorkflowRun.system_queryset().filter(pk=run.pk).update(output={"discard": True})
        if failure is IntegrityError:
            # A real database error poisons this savepoint until the dispatcher rolls it back.
            WorkflowRun.system_queryset().filter(pk=run.pk).update(version_id=None)
        raise failure("Cleanup interrupted")

    @isolated_run_cleanup
    def later(sender, *, run, **kwargs):
        observed.append(WorkflowRun.system_queryset().get(pk=run.pk).status)

    run_stopping.connect(failing)
    run_stopping.connect(later)
    try:
        assert WorkflowRun.objects.cancel(target, actor=actor).canceled
    finally:
        run_stopping.disconnect(failing)
        run_stopping.disconnect(later)
    target.refresh_from_db()
    assert target.status == "canceled" and target.finished_at is not None and target.output == {}
    assert observed == ["failing", "canceled"]
    assert "Workflow cleanup receiver" in caplog.text
    if failure is ValueError:
        assert "Cleanup interrupted" in caplog.text


def test_undecorated_receiver_database_failure_cannot_block_the_terminal_write(target, execution):
    actor, _sent = execution

    def undecorated(sender, *, run, **kwargs):
        # Raw SQL has no savepoint: on PostgreSQL this aborts the transaction,
        # and only the sender's savepoint recovers it.
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM workflow_cleanup_missing_table")

    run_stopping.connect(undecorated)
    try:
        assert WorkflowRun.objects.cancel(target, actor=actor).canceled
    finally:
        run_stopping.disconnect(undecorated)
    target.refresh_from_db()
    assert target.status == "canceled" and target.finished_at is not None


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
    assert StepRun.system_queryset().get(run=target).status == "canceled"
    finished_at = target.finished_at
    tasks.cancel(**cancellations(sent)[0])
    target.refresh_from_db()
    assert target.finished_at == finished_at


def test_cleanup_receiver_keeps_native_sender_duplicate_and_disconnect_behavior(target):
    observed = []

    @isolated_run_cleanup
    def stopping(sender, *, run, **kwargs):
        observed.append(run.pk)
        return "cleaned"

    uid = "test.workflows.run_stopping"
    run_stopping.connect(stopping, sender=WorkflowRun, dispatch_uid=uid)
    run_stopping.connect(stopping, sender=WorkflowRun, dispatch_uid=uid)
    try:
        assert run_stopping.send_robust(sender=StepRun, run=target) == []
        assert run_stopping.send_robust(sender=WorkflowRun, run=target) == [(stopping, "cleaned")]
        assert observed == [target.pk]
    finally:
        assert run_stopping.disconnect(sender=WorkflowRun, dispatch_uid=uid)
    assert run_stopping.send_robust(sender=WorkflowRun, run=target) == []


def test_cleanup_receiver_refuses_async_database_cleanup():
    async def stopping(sender, **kwargs):
        pass

    with pytest.raises(TypeError, match="must be synchronous"):
        isolated_run_cleanup(stopping)


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
    assert runner.execute(StepRun.system_queryset().get(run=caller).pk)
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
