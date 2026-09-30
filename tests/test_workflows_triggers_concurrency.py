"""Real row-lock races between source saves and durable admission."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from django.db import connection, transaction
from rebac import actor_context

from angee.base.scoping import system_queryset
from angee.workflows.testing.drivers import trigger_source
from angee.workflows.testing.models import Trigger, TriggerEvent, WorkflowRun
from tests.conftest import Vault, create_user, vault_for
from tests.test_workflows_review_concurrency import submit, wait_for_lock
from tests.test_workflows_triggers import capture
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.test_workflows_triggers import trigger_setup as trigger_setup

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def test_two_saves_racing_admission_retain_one_ledger_and_run(trigger_setup, monkeypatch):
    """Saves serialize behind admission; subsequent changes cannot admit twice."""
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    event = system_queryset(TriggerEvent).get()
    entered, release = Event(), Event()

    def pause(self, record, *, actor):
        entered.set()
        assert release.wait(15)

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", pause)

    def save():
        with transaction.atomic(), actor_context(actor):
            current = Vault.objects.get(pk=record.pk)
            current.save()
            capture(current)

    with ThreadPoolExecutor(max_workers=3) as pool:
        admission, _ = submit(pool, lambda: Trigger.objects.admit(event))
        try:
            assert entered.wait(10)
            first, pid = submit(pool, save)
            wait_for_lock(pid, first)
            second, other_pid = submit(pool, save)
            wait_for_lock(other_pid, second)
            assert Trigger.objects.drain() == 0  # Busy trigger cannot stall the sweep.
        finally:
            release.set()
        assert admission.result(timeout=10)
        first.result(timeout=10)
        second.result(timeout=10)
    assert Trigger.objects.drain() == 0
    assert system_queryset(TriggerEvent).count() == 1
    run = system_queryset(WorkflowRun).get()
    assert run.request_key == f"trigger:{trigger.sqid}:{record.sqid}"
    event.refresh_from_db()
    assert event.admitted_at and event.run_id == run.pk


def test_reentrant_capture_does_not_lock_other_triggers(trigger_setup, monkeypatch, caplog):
    """Two triggers and two source writers preserve all captures without a lock cycle."""
    actor, workflow, record, lower = trigger_setup
    other = vault_for(create_user("concurrent-source-owner"), name="Ready")
    with actor_context(actor):
        higher = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label="knowledge.vault",
            condition={"name": {"_eq": "Ready"}},
        )
    for trigger in (lower, higher):
        Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    event = system_queryset(TriggerEvent).get(trigger=higher, record_object_id=record.pk)
    entered, release_hook, captured, release_writer = Event(), Event(), Event(), Event()

    def write(self, record, *, actor):
        if self.pk == higher.pk:
            entered.set()
            assert release_hook.wait(15)
        record.save(update_fields=("name",))

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", write)
    def save():
        with transaction.atomic(), actor_context(actor):
            current = Vault.objects.get(pk=other.pk)
            current.save(update_fields=("name",))
            captured.set()
            assert release_writer.wait(15)

    with trigger_source(type(record), connect=True):
        with ThreadPoolExecutor(max_workers=2) as pool:
            admission, _ = submit(pool, lambda: Trigger.objects.admit(event))
            try:
                assert entered.wait(10)
                saved, _ = submit(pool, save)
                assert captured.wait(10)
                release_hook.set()
                assert admission.result(timeout=10)
            finally:
                release_hook.set()
                release_writer.set()
            saved.result(timeout=10)
        assert system_queryset(TriggerEvent).count() == 4
        assert Trigger.objects.drain() == 3
        assert Trigger.objects.drain() == 0
        assert system_queryset(WorkflowRun).count() == 4
        assert "Workflow trigger capture failed" not in caplog.text


def test_domain_hook_write_and_concurrent_save_share_record_first_lock_order(trigger_setup, monkeypatch):
    """A source writer waits before its capture, so a hook can update the same row."""
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    event = system_queryset(TriggerEvent).get()
    entered, release = Event(), Event()

    def write(self, record, *, actor):
        entered.set()
        assert release.wait(15)
        record.name = "Admitted"
        record.save()

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", write)

    def save():
        with transaction.atomic(), actor_context(actor):
            current = Vault.objects.get(pk=record.pk)
            current.name = "Saved afterward"
            current.save()
            capture(current)

    with ThreadPoolExecutor(max_workers=2) as pool:
        admission, _ = submit(pool, lambda: Trigger.objects.admit(event))
        try:
            assert entered.wait(10)
            saved, pid = submit(pool, save)
            wait_for_lock(pid, saved)
            assert Trigger.objects.drain() == 0
        finally:
            release.set()
        assert admission.result(timeout=10)
        saved.result(timeout=10)
    record.refresh_from_db()
    assert record.name == "Saved afterward"
    event.refresh_from_db()
    assert event.admitted_at and event.run_id and not event.rejection
    assert system_queryset(WorkflowRun).count() == 1
