"""Native PostgreSQL source saves against watch planning and cancellation."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier, Event

import pytest
from django.db import connection, transaction
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.workflows import tasks
from angee.workflows.steps import Wait
from angee.workflows.testing.drivers import load_workflow, run_until, start_run
from angee.workflows.testing.models import StepAttempt, StepRun, StepWatch, WorkflowRun
from tests.conftest import Vault, create_user, vault_for
from tests.test_workflows_review_concurrency import submit, wait_for_lock
from tests.test_workflows_watches import Watch, record_deliveries, start_watcher
from tests.test_workflows_watches import watched_source as watched_source
from tests.workflow_steps import document

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


@pytest.mark.parametrize("atomic_save", [True, False], ids=["transaction", "autocommit"])
def test_two_native_saves_during_watch_planning_wake_once(watched_source, register_step, atomic_save):
    """Both source writers wait for registration; duplicate wakes enqueue one execution."""
    actor, sent, record = watched_source
    planned, release = Event(), Event()

    class PlanningWatch(Watch):
        key = "planning_watch"

        def run(self, ctx):
            result = super().run(ctx)
            if isinstance(result, Wait):
                planned.set()
                assert release.wait(15)
            return result

    register_step(PlanningWatch)
    run, step = start_watcher(PlanningWatch, record, actor)
    sent.clear()

    def save():
        with transaction.atomic() if atomic_save else nullcontext(), actor_context(actor):
            current = Vault.objects.get(pk=record.pk)
            current.name = "Ready"
            current.save(update_fields=("name",))

    with ThreadPoolExecutor(max_workers=3) as pool:
        planner, _ = submit(pool, lambda: StepRun.objects.execute(step.pk))
        try:
            assert planned.wait(10)
            first, first_pid = submit(pool, save)
            wait_for_lock(first_pid, first)
            second, second_pid = submit(pool, save)
            wait_for_lock(second_pid, second)
            assert not record_deliveries(sent)
        finally:
            release.set()
        assert planner.result(timeout=10)
        first.result(timeout=10)
        second.result(timeout=10)
        step.refresh_from_db()
        assert (step.status, step.waiting_kind) == ("waiting", "record")
        assert system_queryset(StepWatch).filter(step_run=step, pending=True).count() == 1
        deliveries = record_deliveries(sent)
        assert len(deliveries) == 2
        together = Barrier(2)

        def deliver(payload):
            together.wait(timeout=10)
            return tasks.wake_records(**payload)

        wakes = [submit(pool, lambda payload=payload: deliver(payload))[0] for payload in deliveries]
        assert sorted(worker.result(timeout=10) for worker in wakes) == [0, 1]

    step.refresh_from_db()
    assert step.status == "ready"
    assert sum(name == "workflows.execute" for name, _ in sent) == 1
    assert system_queryset(StepAttempt).filter(step_run=step).count() == 1
    assert StepRun.objects.wake_records() == 0
    run_until(run)
    assert run.status == "succeeded" and run.output == {"value": 7}
    assert system_queryset(StepAttempt).filter(step_run=step).count() == 2
    assert not system_queryset(StepWatch).exists()


def test_native_save_racing_cancellation_cannot_resurrect_the_waiter(watched_source, register_step):
    """Cancellation waits for source capture; commit delivery never reverses run finality."""
    actor, sent, record = watched_source
    register_step(Watch)
    run, step = start_watcher(Watch, record, actor)
    run_until(run)
    sent.clear()
    captured, release = Event(), Event()

    def save():
        with transaction.atomic(), actor_context(actor):
            current = Vault.objects.get(pk=record.pk)
            current.name = "Ready"
            current.save(update_fields=("name",))
            captured.set()
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, _ = submit(pool, save)
        try:
            assert captured.wait(10)
            canceled, pid = submit(pool, lambda: WorkflowRun.objects.cancel(run, actor=actor))
            wait_for_lock(pid, canceled)
            assert not record_deliveries(sent)
        finally:
            release.set()
        saved.result(timeout=10)
        assert canceled.result(timeout=10).canceled

    run.refresh_from_db()
    terminal = (run.status, run.outcome, run.output, run.finished_at)
    assert run.status == "canceled"
    step.refresh_from_db()
    assert step.status == "canceled"
    assert not system_queryset(StepWatch).exists()
    deliveries = record_deliveries(sent)
    assert len(deliveries) == 1
    assert tasks.wake_records(**deliveries[0]) == StepRun.objects.wake_records() == 0
    assert not any(name == "workflows.execute" for name, _ in sent)
    run.refresh_from_db()
    assert (run.status, run.outcome, run.output, run.finished_at) == terminal


def test_registration_rechecks_read_after_waiting_for_an_ownership_transfer(watched_source, register_step):
    """An old readable snapshot cannot register a watch after its owner changes."""
    admin, _sent, _record = watched_source
    starter, successor = create_user("watch-former-owner"), create_user("watch-new-owner")
    record = vault_for(starter, name="Transferred during registration")
    transferred, observed, release = Event(), Event(), Event()

    class TransferWatch(Watch):
        key = "transfer_watch"

        def run(self, ctx):
            previous = ctx.subject
            observed.set()
            ctx.watch(previous)
            return ctx.wait()

    register_step(TransferWatch)
    workflow = load_workflow(document("entry", step=TransferWatch.key), actor=admin,
                             subject_model=record._meta.label_lower)
    workflow.with_actor(admin).grant_record_access("starter", starter)
    run = start_run(workflow, actor=starter, subject=record, input={"value": 7})
    step = system_queryset(StepRun).get(run=run)

    def transfer():
        with transaction.atomic(), actor_context(starter):
            current = Vault.objects.lock_if_supported(no_key=True).get(pk=record.pk)
            current.owner = successor
            current.save(update_fields=("owner",))
            transferred.set()
            assert release.wait(15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer, _ = submit(pool, transfer)
        try:
            assert transferred.wait(10)
            planner, pid = submit(pool, lambda: StepRun.objects.execute(step.pk))
            assert observed.wait(10)
            wait_for_lock(pid, planner)
        finally:
            release.set()
        writer.result(timeout=10)
        assert planner.result(timeout=10)

    assert not Vault.objects.with_actor(starter).filter(pk=record.pk).exists()
    step.refresh_from_db()
    assert step.status == "failed"
    assert "Read access" in system_queryset(StepAttempt).get(step_run=step).error
    assert not system_queryset(StepWatch).filter(step_run=step).exists()
