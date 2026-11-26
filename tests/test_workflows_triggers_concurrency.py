"""Real row-lock races between source saves, durable admission, operator verbs and resource loads."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
import yaml
from django.db import connection, transaction
from rebac import RelationshipTuple, actor_context, to_object_ref, to_subject_ref, write_relationships
from rebac.models import active_relationship_model

from angee.resources.testing.models import Resource
from angee.workflows.testing.models import Trigger, TriggerEvent, Workflow, WorkflowRun
from tests.conftest import Vault, create_user, make_addon, vault_for, write_addon_package
from tests.lock_waits import submit, wait_for_lock
from tests.test_workflows_triggers import capture
from tests.test_workflows_triggers import trigger_resource_schema as trigger_resource_schema
from tests.test_workflows_triggers import trigger_setup as trigger_setup
from tests.test_workflows_triggers_surface import InstalledTriggers
from tests.workflow_steps import document

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("workflow_step_classes"),
    pytest.mark.skipif(connection.vendor != "postgresql", reason="Real PostgreSQL row locks are required."),
]


def grant_principal_record_write(record, workflow):
    """Permit the writing admission hooks to save as the workflow principal."""
    write_relationships([RelationshipTuple(
        resource=to_object_ref(record), relation="editor", subject=to_subject_ref(workflow.user),
    )])


def test_two_saves_racing_admission_retain_one_ledger_and_run(trigger_setup, monkeypatch):
    """Saves serialize behind admission; subsequent changes cannot admit twice."""
    actor, _, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    event = TriggerEvent.system_queryset().get()
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
    assert TriggerEvent.system_queryset().count() == 1
    run = WorkflowRun.system_queryset().get()
    assert run.request_key == f"trigger:{trigger.sqid}:{record.sqid}"
    event.refresh_from_db()
    assert event.admitted_at and event.started_run.pk == run.pk


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
    grant_principal_record_write(record, workflow)
    grant_principal_record_write(other, workflow)
    capture(record)
    event = TriggerEvent.system_queryset().get(trigger=higher, record_object_id=record.pk)
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
    assert TriggerEvent.system_queryset().count() == 4
    assert Trigger.objects.drain() == 3
    assert Trigger.objects.drain() == 0
    assert WorkflowRun.system_queryset().count() == 4
    assert "Workflow trigger capture failed" not in caplog.text


def test_domain_hook_write_and_concurrent_save_share_record_first_lock_order(trigger_setup, monkeypatch):
    """A source writer waits before its capture, so a hook can update the same row."""
    actor, workflow, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    grant_principal_record_write(record, workflow)
    capture(record)
    event = TriggerEvent.system_queryset().get()
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
    assert event.admitted_at and not event.rejection
    assert event.started_run.pk == WorkflowRun.system_queryset().get().pk


def test_admission_holds_its_grant_while_other_triggers_enable(trigger_setup, monkeypatch):
    """Admission leaves the workflow row free, and disable waits for its trigger lock."""
    actor, workflow, record, trigger = trigger_setup
    Trigger.objects.enable(trigger, actor=actor)
    capture(record)
    event = TriggerEvent.system_queryset().get(trigger=trigger)
    entered, release = Event(), Event()
    grant = active_relationship_model().objects.filter(
        resource_type="knowledge/role", resource_id="vault_viewer", relation="member",
        subject_id=str(workflow.user_id),
    )

    def check(self, source, *, actor):
        assert grant.exists()
        entered.set()
        assert release.wait(15)
        assert grant.exists()

    monkeypatch.setattr(Trigger, "trigger_sources", ("record_changed",), raising=False)
    monkeypatch.setattr(Trigger, "check_admission", check)
    with actor_context(actor):
        other = Trigger.objects.create(
            workflow=workflow, source="record_changed", model_label="knowledge.vault",
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        admission, _ = submit(pool, lambda: Trigger.objects.admit(event))
        try:
            assert entered.wait(10)
            enabled, _ = submit(pool, lambda: Trigger.objects.enable(other, actor=actor))
            assert enabled.result(timeout=10).enabled  # The workflow row is not locked by admission.
            disabled, pid = submit(pool, lambda: Trigger.objects.disable(trigger, actor=actor))
            wait_for_lock(pid, disabled)
        finally:
            release.set()
        assert admission.result(timeout=10)
        disabled.result(timeout=10)
    assert event.started_run.run_as_id == workflow.user_id
    assert grant.exists()  # The other enabled trigger still contributes this tuple.


@pytest.mark.parametrize("verb", ["enable", "disable"])
def test_a_load_reapplying_a_trigger_waits_for_an_operators_verb_and_keeps_its_outcome(
    execution, trigger_resource_schema, tmp_path, verb,
):
    """The operator first: the load reads the activation it keeps once the operator's verb commits."""
    admin, _sent = execution
    release = InstalledTriggers(tmp_path)
    release.load()
    record, _schedule = release.triggers()
    if verb == "disable":
        Trigger.objects.enable(record, actor=admin)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            getattr(Trigger.objects, verb)(record, actor=admin)
            loading, pid = submit(pool, lambda: release.load(create_only=("condition",)))
            wait_for_lock(pid, loading)
        assert loading.result(timeout=30).updated == 1

    stored = Trigger.system_queryset().get(pk=record.pk)
    assert (stored.enabled, bool(stored.granted_targets)) == (verb == "enable",) * 2


def pause_trigger_saves(monkeypatch):
    """Hold each trigger save of the load, after the adapter's locks, until the returned event is set."""
    entered, go = Event(), Event()
    save = Trigger.save

    def paused(self, *args, **kwargs):
        entered.set()
        assert go.wait(15)
        return save(self, *args, **kwargs)

    monkeypatch.setattr(Trigger, "save", paused)
    return entered, go


@pytest.mark.parametrize("verb", ["enable", "disable"])
def test_an_operators_verb_waits_for_a_load_that_reapplies_its_trigger_and_its_outcome_stands(
    execution, trigger_resource_schema, tmp_path, monkeypatch, verb,
):
    """The load first: it holds the workflow, then the trigger, before it saves, so the verb waits on the workflow."""
    admin, _sent = execution
    release = InstalledTriggers(tmp_path)
    release.load()
    record, _schedule = release.triggers()
    if verb == "disable":
        Trigger.objects.enable(record, actor=admin)
    entered, go = pause_trigger_saves(monkeypatch)

    with ThreadPoolExecutor(max_workers=2) as pool:
        loading, _pid = submit(pool, lambda: release.load(create_only=("condition",)))
        try:
            assert entered.wait(10)
            operating, pid = submit(pool, lambda: getattr(Trigger.objects, verb)(record, actor=admin))
            wait_for_lock(pid, operating)
        finally:
            go.set()
        assert loading.result(timeout=30).updated == 1
        operating.result(timeout=30)

    stored = Trigger.system_queryset().get(pk=record.pk)
    assert (stored.enabled, bool(stored.granted_targets)) == (verb == "enable",) * 2


@pytest.mark.parametrize("trigger_reapplied", [False, True], ids=["trigger-skipped", "trigger-reapplied"])
def test_a_load_locks_workflows_in_its_rows_order_so_a_writer_taking_two_in_that_order_never_deadlocks(
    execution, trigger_resource_schema, tmp_path, trigger_reapplied,
):
    """A renamed first workflow and a schedule on the second: the load takes the first, then the second, if at all."""
    workflows, triggers = "install/000_workflows.workflow.yaml", "install/100_workflows.trigger.yaml"
    (tmp_path / "resources" / "install").mkdir(parents=True)
    owner = make_addon(path=tmp_path, resources={"install": [{"path": workflows}, {"path": triggers}]})

    def declare(first_name, create_only=()):
        entry = {"path": triggers, "depends_on": workflows}
        if create_only:
            entry["create_only"] = list(create_only)
        write_addon_package(owner, resources={"install": [{"path": workflows}, entry]})
        (tmp_path / "resources" / workflows).write_text(yaml.safe_dump({"rows": [
            {"xref": key, "fields": {"key": f"ordered-{key}", "name": name, "draft": document("entry")}}
            for key, name in (("first", first_name), ("second", "Second"))
        ]}))
        (tmp_path / "resources" / triggers).write_text(yaml.safe_dump({"rows": [{"xref": "schedule", "fields": {
            "workflow": f"{owner.name}.second", "source": "schedule", "recurrence": "FREQ=HOURLY",
            "recurrence_start": "2026-01-01 00:30:00",
        }}]}))
        return lambda: Resource.objects.load_addons((owner,), tiers=[Resource.Tier.INSTALL])

    declare("First")()
    first, second = (Workflow.system_queryset().get(key=f"ordered-{key}").pk for key in ("first", "second"))
    load = declare("First renamed", ("recurrence",) if trigger_reapplied else ())

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            Trigger.objects.lock_grants(first)
            loading, pid = submit(pool, load)
            wait_for_lock(pid, loading)
            Trigger.objects.lock_grants(second)
        result = loading.result(timeout=30)

    assert (result.updated, result.skipped) == ((2, 1) if trigger_reapplied else (1, 2))


def test_a_trigger_row_the_load_leaves_unchanged_is_not_locked_so_the_schedule_tick_records_meanwhile(
    execution, trigger_resource_schema, tmp_path, monkeypatch,
):
    admin, _sent = execution
    release = InstalledTriggers(tmp_path)
    release.load()
    _record, schedule = release.triggers()
    Trigger.objects.enable(schedule, actor=admin)
    missed = Trigger.system_queryset().get(pk=schedule.pk).next_occurrence_at - timedelta(hours=2)
    Trigger.system_queryset().filter(pk=schedule.pk).update(next_occurrence_at=missed, enabled_at=missed)
    entered, go = pause_trigger_saves(monkeypatch)

    with ThreadPoolExecutor(max_workers=1) as pool:
        loading, _pid = submit(pool, lambda: release.load(create_only=("condition",)))
        try:
            assert entered.wait(10)
            recorded = Trigger.objects.record_occurrences()
        finally:
            go.set()
        assert (loading.result(timeout=30).updated, recorded) == (1, 2)
    assert Trigger.system_queryset().get(pk=schedule.pk).enabled
