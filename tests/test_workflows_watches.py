"""Transactional record observation, shared wake recovery and permission-safe reads."""

from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import transaction
from django.db.models.functions import Now
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.workflows import schema as workflow_schema
from angee.workflows import tasks
from angee.workflows.runner import runner
from angee.workflows.steps import StepMode
from angee.workflows.testing.drivers import load_workflow, run_until, start_run, trigger_source
from angee.workflows.testing.models import StepAttempt, StepRun, StepWatch, WorkflowRun
from tests.conftest import Vault, addon_schema, create_user, execute_schema, result_data, vault_for
from tests.workflow_steps import Echo, document

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("workflow_step_classes")]


@pytest.fixture
def watched_source(execution):
    """Use the native save signal and restore both its opt-in and receiver afterward."""
    actor, sent = execution
    with trigger_source(Vault, connect=True):
        record = vault_for(actor, name="Waiting")
        yield actor, sent, record


def start_watcher(step, record, actor, *, key="watch"):
    """Admit a real single-node run against a typed subject."""
    workflow = load_workflow(document("entry", step=step.key), key=key, actor=actor,
                             subject_model=record._meta.label_lower)
    run = start_run(workflow, actor=actor, subject=record, input={"value": 7})
    return run, system_queryset(StepRun).get(run=run)


def record_deliveries(sent):
    """Expose only the shared watch wake task payloads captured after actual commits."""
    return [envelope["kwargs"] for name, envelope in sent if name == "workflows.wake_records"]


class Watch(Echo):
    """Read the predicate under the same target lock that installs observation."""
    key = "watch_value"

    def run(self, ctx):
        record = ctx.subject_for_update()
        if record.name == "Ready":
            return ctx.done(ctx.input)
        ctx.watch(record, record)
        return ctx.wait(state={"observed": True})


def test_saved_record_wakes_once_rearms_and_then_settles(watched_source, register_step):
    actor, sent, record = watched_source
    register_step(Watch)
    run, step = start_watcher(Watch, record, actor)
    run_until(run)
    step.refresh_from_db()
    assert (step.status, step.waiting_kind, step.wake_at) == ("waiting", "record", None)
    assert step.state == {"observed": True} and step.retries == 0
    assert system_queryset(StepWatch).filter(step_run=step).count() == 1
    sent.clear()
    with transaction.atomic(), actor_context(actor):
        record.save(update_fields=("name",))
        record.save(update_fields=("name",))
        assert not record_deliveries(sent)
    assert len(record_deliveries(sent)) == 2
    assert [tasks.wake_records(**payload) for payload in record_deliveries(sent)] == [1, 0]
    assert not system_queryset(StepWatch).exists()
    run_until(run)
    assert system_queryset(StepWatch).filter(step_run=step, pending=False).count() == 1
    with actor_context(actor):
        record.name = "Ready"
        record.save(update_fields=("name",))
    assert runner.wake_records() == 1
    run_until(run)
    run.refresh_from_db()
    assert run.status == "succeeded" and run.output == {"value": 7}
    assert not system_queryset(StepWatch).exists()


def test_rolled_back_save_does_not_wake_and_tick_recovers_lost_delivery(watched_source, register_step):
    actor, sent, record = watched_source
    register_step(Watch)
    run, _step = start_watcher(Watch, record, actor)
    run_until(run)
    sent.clear()
    with transaction.atomic(), actor_context(actor):
        record.save()
        transaction.set_rollback(True)
    assert not record_deliveries(sent)
    assert not system_queryset(StepWatch).filter(pending=True).exists()
    with actor_context(actor):
        record.save()
    sent.clear()  # Simulate a lost broker message; the native pending fact remains.
    assert runner.tick()["records"] == 1
    assert runner.tick()["records"] == 0
    assert sum(name == "workflows.execute" for name, _ in sent) == 1


@pytest.mark.parametrize("ending", ["done", "fail", "invalid", "next_page", "cancel"])
def test_watch_cleanup_follows_settlement_cancel_and_prune(watched_source, register_step, ending):
    actor, _sent, record = watched_source

    class Observe(Echo):
        key = "watch_cleanup"

        def run(self, ctx):
            if ctx.step_run.page_index:
                return ctx.done(ctx.input)
            ctx.watch(ctx.subject)
            if ending == "fail":
                return ctx.fail("Discard registration.")
            if ending == "invalid":
                return ctx.done({"value": "invalid"})
            if ending == "next_page":
                return ctx.next_page()
            return ctx.done(ctx.input) if ending == "done" else ctx.wait()

    register_step(Observe)
    run, step = start_watcher(Observe, record, actor)
    run_until(run)
    if ending == "cancel":
        assert system_queryset(StepWatch).filter(step_run=step).exists()
        WorkflowRun.objects.cancel(run, actor=actor)
    assert not system_queryset(StepWatch).exists()
    if ending in {"fail", "invalid"}:
        assert system_queryset(StepAttempt).get(step_run=step).result == "failed"


def test_retention_cascades_watches_from_a_failed_runs_open_branch(watched_source, register_step):
    actor, _sent, record = watched_source
    register_step(Watch)
    graph = {
        "nodes": {
            "entry": {"step": "echo", "next": {"done": ["watch", "failure"]}},
            "watch": {"step": Watch.key},
            "failure": {"step": "reject"},
        },
        "results": [{"from": "watch"}],
    }
    workflow = load_workflow(graph, actor=actor, subject_model=record._meta.label_lower)
    run = start_run(workflow, actor=actor, subject=record, input={"value": 7})
    run_until(run, node="watch")
    waiter = system_queryset(StepRun).get(run=run, node_key="watch")
    assert runner.execute(waiter.pk)
    run_until(run)
    assert run.status == "failed"
    assert system_queryset(StepWatch).filter(step_run=waiter).exists()
    system_queryset(WorkflowRun).filter(pk=run.pk).update(finished_at=Now() - timedelta(days=91))
    assert WorkflowRun.objects.prune() == 1
    assert not system_queryset(StepWatch).exists()


@pytest.mark.parametrize("case", ["timeout", "empty", "io", "not_opted", "same_body_change"])
def test_record_wait_admission_and_deadline_recovery(watched_source, register_step, case):
    actor, _sent, record = watched_source

    class Observe(Echo):
        key = "watch_contract"
        mode = StepMode.IO if case == "io" else StepMode.DATABASE

        def run(self, ctx):
            if case != "empty":
                ctx.watch(ctx.subject)
            if case == "same_body_change":
                ctx.subject.save()
            return ctx.wait(until=ctx.now - timedelta(seconds=1) if case == "timeout" else None)

    register_step(Observe)
    _run, step = start_watcher(Observe, record, actor)
    with patch.object(Vault, "record_changed_enabled", False) if case == "not_opted" else nullcontext():
        assert runner.execute(step.pk)
    step.refresh_from_db()
    if case in {"empty", "io", "not_opted"}:
        assert step.status == "failed" and not system_queryset(StepWatch).exists()
    else:
        assert step.status == "waiting" and step.waiting_kind == "record"
        assert (runner.wake() if case == "timeout" else runner.wake_records()) == 1
        assert not system_queryset(StepWatch).exists()


@pytest.mark.parametrize("access", ["readable", "private", "unsaved"])
def test_registration_requires_the_run_actors_read_permission(watched_source, register_step, access):
    admin, _sent, private = watched_source
    starter = create_user("watch-starter")
    subject = vault_for(starter, name="First watched record")
    target = (vault_for(starter, name="Second watched record") if access == "readable"
              else private if access == "private" else Vault(name="Unsaved record"))

    class Observe(Echo):
        key = "watch_as_actor"

        def run(self, ctx):
            ctx.watch(ctx.subject, target)
            return ctx.wait()

    register_step(Observe)
    workflow = load_workflow(document("entry", step=Observe.key), actor=admin,
                             subject_model=subject._meta.label_lower)
    workflow.with_actor(admin).grant_record_access("starter", starter)
    run = start_run(workflow, actor=starter, subject=subject, input={"value": 7})
    run_until(run)
    if access == "readable":
        assert system_queryset(StepWatch).filter(step_run__run=run).count() == 2
        with actor_context(starter):
            subject.save()
            target.save()
        assert runner.wake_records() == 1
        assert runner.wake_records() == 0
    else:
        assert run.status == "failed" and not system_queryset(StepWatch).exists()
        assert "Read access" in system_queryset(StepAttempt).get(step_run__run=run).error


def test_watch_resources_follow_run_reads_and_record_reference_owner(
    watched_source, register_step, workflow_permissions,
):
    actor, _sent, record = watched_source
    register_step(Watch)
    run, step = start_watcher(Watch, record, actor)
    run_until(run)
    reader, stranger = create_user("watch-reader"), create_user("watch-stranger")
    run.with_actor(actor).grant_record_access("reader", reader)
    schema = addon_schema(workflow_schema.schemas, "console")
    query = """query($step: String!) {
      stepwatch(where: {step_run: {_eq: $step}}) { record_model record_id }
      steprun(where: {id: {_eq: $step}}) { watches { record_model record_id } }
    }"""
    reference = {"record_model": record._meta.label, "record_id": record.sqid}
    for viewer in (actor, reader):
        assert result_data(execute_schema(schema, query, {"step": step.sqid}, user=viewer)) == {
            "stepwatch": [reference], "steprun": [{"watches": [reference]}],
        }
    assert result_data(execute_schema(schema, query, {"step": step.sqid}, user=stranger)) == {
        "stepwatch": [], "steprun": [],
    }
