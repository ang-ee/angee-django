"""Schedule triggers: a cursor the tick follows, one run per occurrence, missed ones skipped."""

from datetime import UTC, datetime, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.workflows import models as workflow_models
from angee.workflows import schema as workflow_schema
from angee.workflows import triggers
from angee.workflows.runner import runner
from angee.workflows.states import RunOrigin, RunStatus
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import Trigger, TriggerEvent, WorkflowRun
from tests.conftest import Vault, addon_schema, create_user, vault_for
from tests.workflow_steps import document

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("workflow_step_classes")]

DAY_ONE = datetime(2026, 1, 5, 9, tzinfo=UTC)


@pytest.fixture
def scheduled(execution, settings):
    """A subjectless published workflow, expanded on UTC wall clocks so instants read as authored."""
    settings.TIME_ZONE = "UTC"
    actor, _sent = execution
    return actor, load_workflow(document("entry"), key="nightly-digest", actor=actor)


def schedule(workflow, actor, **fields):
    """Author a schedule trigger through the validating model save."""
    with actor_context(actor):
        return Trigger.objects.create(workflow=workflow, source="schedule", **fields)


def hourly(workflow, actor, *, missed=0):
    """Enable an hourly schedule on the half hour; move its cursor back as if workers were down for ``missed`` hours."""
    now = timezone.now()
    trigger = Trigger.objects.enable(
        schedule(workflow, actor, recurrence="FREQ=HOURLY", recurrence_start=now - timedelta(hours=10, minutes=30)),
        actor=actor,
    )
    if missed:
        cursor = trigger.next_occurrence_at - timedelta(hours=missed)
        Trigger.system_queryset().filter(pk=trigger.pk).update(next_occurrence_at=cursor, enabled_at=cursor)
    return Trigger.system_queryset().get(pk=trigger.pk)


def occurrence_states(trigger):
    """Each recorded occurrence in order, with whether it was skipped or admitted."""
    return [
        (event.occurs_at, event.skipped_at is not None, event.admitted_at is not None)
        for event in TriggerEvent.system_queryset().filter(trigger=trigger).order_by("occurs_at")
    ]


def test_schedule_configuration_names_a_recurrence_and_no_record_scope(scheduled):
    actor, workflow = scheduled
    daily = {"recurrence": "FREQ=DAILY", "recurrence_start": DAY_ONE}
    refusals = (
        ({"recurrence": "FREQ=DAILY"}, "needs its start"),
        ({**daily, "model_label": "knowledge.vault"}, "admits no record"),
        ({**daily, "condition": {"name": {"_eq": "x"}}}, "admits no record"),
        ({**daily, "recurrence": "FREQ=SOMETIMES"}, "recurrence rule"),
        ({**daily, "recurrence_zone": "Nowhere/Town"}, "IANA time zone"),
    )
    for fields, message in refusals:
        with pytest.raises(ValidationError, match=message):
            schedule(workflow, actor, **fields)
    subject_workflow = load_workflow(
        document("entry"), key="subject-digest", actor=actor, subject_model="knowledge.vault",
    )
    with pytest.raises(ValidationError, match="without a subject"):
        schedule(subject_workflow, actor, **daily)
    with actor_context(actor), pytest.raises(ValidationError, match="Only a schedule trigger"):
        Trigger.objects.create(
            workflow=subject_workflow, source="record_changed", model_label="knowledge.vault", recurrence="FREQ=DAILY",
        )
    zoned = schedule(workflow, actor, **daily, recurrence_zone="Europe/Brussels")
    assert str(zoned) == "Schedule: FREQ=DAILY" and zoned.source_model == ""
    assert str(schedule(workflow, actor, recurrence="", recurrence_start=DAY_ONE)) == "Schedule"


def test_enabling_sets_the_cursor_without_source_grants_and_disabling_clears_it(scheduled):
    actor, workflow = scheduled
    start = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
    trigger = schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=start)
    assert Trigger.objects.enable_preview(trigger, actor=actor).grants == ()
    enabled = Trigger.objects.enable(trigger, actor=actor)
    assert enabled.enabled and enabled.granted_targets == [] and not enabled.granted_relationships(actor=actor)
    assert enabled.next_occurrence_at == start and enabled.enabled_at <= start
    repaired = Trigger.objects.enable(enabled, actor=actor)
    assert (repaired.enabled_at, repaired.next_occurrence_at) == (enabled.enabled_at, enabled.next_occurrence_at)
    with pytest.raises(ValidationError, match="Disable the trigger"), actor_context(actor):
        enabled.recurrence = "FREQ=WEEKLY"
        enabled.save()
    disabled = Trigger.objects.disable(enabled, actor=actor)
    assert not disabled.enabled and disabled.enabled_at is None and disabled.next_occurrence_at is None


def test_a_stale_instance_save_keeps_the_stored_enablement_and_cursor(scheduled):
    actor, workflow = scheduled
    stale = schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=timezone.now() + timedelta(days=1))
    enabled = Trigger.objects.enable(stale, actor=actor)
    Trigger.objects.disable(enabled, actor=actor)
    Trigger.objects.enable(enabled, actor=actor)
    current = Trigger.system_queryset().get(pk=stale.pk)
    with actor_context(actor):
        Trigger.objects.get(pk=stale.pk).save(update_fields=["updated_at"])
    saved = Trigger.system_queryset().get(pk=stale.pk)
    assert (saved.enabled_at, saved.next_occurrence_at) == (current.enabled_at, current.next_occurrence_at)


@pytest.mark.parametrize(("rule", "start"), [
    pytest.param("", timedelta(seconds=-30), id="one-off-start-passed"),
    pytest.param("FREQ=DAILY;UNTIL=20260101T000000Z", timedelta(days=-400), id="rule-ended"),
    pytest.param("FREQ=DAILY;BYMONTH=2;BYMONTHDAY=30", timedelta(days=-1), id="never-matches"),
])
def test_a_schedule_that_never_occurs_after_now_cannot_be_enabled(scheduled, rule, start):
    actor, workflow = scheduled
    trigger = schedule(workflow, actor, recurrence=rule, recurrence_start=timezone.now() + start)
    clock = perf_counter()
    with pytest.raises(ValidationError, match="never occurs after now"):
        Trigger.objects.enable(trigger, actor=actor)
    assert perf_counter() - clock < 1
    assert Trigger.objects.enable_preview(trigger, actor=actor) is None
    assert not Trigger.system_queryset().get(pk=trigger.pk).enabled


def test_a_schedule_cannot_be_enabled_for_a_workflow_needing_input_it_does_not_supply(scheduled, register_step):
    actor, _workflow = scheduled
    from pydantic import BaseModel

    from angee.workflows.steps import Step

    class Required(BaseModel):
        name: str

    class NeedsName(Step[Required, None, None]):
        key = "schedule_needs_name"

        def run(self, ctx):
            return ctx.done({})

    register_step(NeedsName)
    workflow = load_workflow(document("entry", step=NeedsName.key), key="named-digest", actor=actor)
    trigger = schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=timezone.now() + timedelta(days=1))
    with pytest.raises(ValidationError, match="name"):
        Trigger.objects.enable(trigger, actor=actor)


def test_tick_starts_one_run_for_the_latest_missed_occurrence(scheduled):
    actor, workflow = scheduled
    trigger = hourly(workflow, actor, missed=3)
    first = runner.tick()
    assert (first["scheduled"], first["drained"]) == (3, 1)
    assert [(skipped, admitted) for _, skipped, admitted in occurrence_states(trigger)] == [
        (True, False), (True, False), (False, True),
    ]
    latest = TriggerEvent.system_queryset().filter(trigger=trigger).latest("occurs_at")
    moved = Trigger.system_queryset().get(pk=trigger.pk).next_occurrence_at
    assert moved == latest.occurs_at + timedelta(hours=1) and moved > timezone.now()
    run = WorkflowRun.system_queryset().get(trigger_event=latest)
    assert run.origin == RunOrigin.TRIGGER and run.subject_object_id is None
    assert run.run_as_id == workflow.user_id
    assert run.request_key == f"trigger:{trigger.sqid}:{latest.occurs_at.isoformat()}"
    second = runner.tick()
    assert (second["scheduled"], second["drained"]) == (0, 0)
    # Recording an occurrence again, as a restarted worker might, adds no row and no run.
    with transaction.atomic(), triggers.system_context(reason="test.replay"):
        replayed = [latest.occurs_at]
        assert TriggerEvent.objects.record_occurrences(trigger, replayed, supersede_before=replayed[0]) == 0
    assert Trigger.objects.drain() == 0
    assert WorkflowRun.system_queryset().filter(version__workflow=workflow).count() == 1
    assert run_until(run).status == RunStatus.SUCCEEDED


def test_a_catch_up_longer_than_a_page_continues_next_tick_and_runs_only_the_latest(scheduled, monkeypatch):
    actor, workflow = scheduled
    monkeypatch.setattr(workflow_models, "SCHEDULE_PAGE_LIMIT", 2)
    trigger = hourly(workflow, actor, missed=5)
    assert [runner.tick()["scheduled"] for _ in range(4)] == [2, 2, 1, 0]
    states = occurrence_states(trigger)
    assert len(states) == 5 and [admitted for _, _, admitted in states] == [False] * 4 + [True]
    assert all(skipped for _, skipped, _ in states[:4])
    assert WorkflowRun.system_queryset().filter(version__workflow=workflow).count() == 1


def test_idle_schedules_add_no_statement_or_audit_row_to_the_tick(scheduled):
    actor, workflow = scheduled

    def tick_statements():
        with CaptureQueriesContext(connection) as captured:
            runner.tick()
        statements = [query["sql"] for query in captured.captured_queries]
        return len(statements), sum("permissionauditevent" in sql for sql in statements)

    runner.tick()
    baseline = tick_statements()
    future = timezone.now() + timedelta(days=30)
    for _ in range(10):
        Trigger.objects.enable(schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=future), actor=actor)
    runner.tick()
    assert tick_statements() == baseline


def test_disabling_skips_pending_occurrences_and_admission_refuses_one_older_than_the_enablement(scheduled):
    actor, workflow = scheduled
    trigger = hourly(workflow, actor, missed=2)
    assert Trigger.objects.record_occurrences() == 2
    pending = TriggerEvent.system_queryset().filter(trigger=trigger).pending().get()
    Trigger.objects.disable(trigger, actor=actor)
    assert not TriggerEvent.system_queryset().filter(trigger=trigger).pending().exists()
    reenabled = Trigger.objects.enable(trigger, actor=actor)
    assert reenabled.next_occurrence_at > timezone.now() and runner.tick()["drained"] == 0
    # A row a racing pass recorded before this enablement is refused, not run.
    stale = TriggerEvent.system_queryset().create(trigger=trigger, occurs_at=pending.occurs_at - timedelta(minutes=1))
    assert Trigger.objects.drain() == 0
    stale.refresh_from_db()
    assert "predates the schedule's current enablement" in stale.rejection
    assert not WorkflowRun.system_queryset().filter(version__workflow=workflow).exists()


def test_a_schedule_unrolls_in_its_zone_across_both_dst_changes(scheduled):
    actor, workflow = scheduled
    brussels = ZoneInfo("Europe/Brussels")
    trigger = schedule(
        workflow, actor, recurrence="FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0",
        recurrence_start=datetime(2026, 3, 27, 9, tzinfo=brussels), recurrence_zone="Europe/Brussels",
    )
    for first, until, hours in (
        (datetime(2026, 3, 27, 9, tzinfo=brussels), datetime(2026, 3, 31, tzinfo=UTC), [8, 8, 7, 7]),
        (datetime(2026, 10, 23, 9, tzinfo=brussels), datetime(2026, 10, 27, tzinfo=UTC), [7, 7, 8, 8]),
    ):
        trigger.next_occurrence_at = first
        moments = trigger.due_occurrences(until)
        assert [moment.astimezone(UTC).hour for moment in moments] == hours
        assert {moment.astimezone(brussels).hour for moment in moments} == {9}


def test_schedule_hooks_receive_the_occurrence_being_admitted(scheduled, monkeypatch):
    actor, workflow = scheduled
    seen = []

    def trigger_input(self, record, *, occurrence):
        assert record is None
        return {"value": occurrence.hour}

    def check_admission(self, record, *, actor, occurrence):
        seen.append(occurrence)

    monkeypatch.setattr(Trigger, "trigger_sources", ("schedule",), raising=False)
    monkeypatch.setattr(Trigger, "trigger_input", trigger_input)
    monkeypatch.setattr(Trigger, "check_admission", check_admission)
    trigger = hourly(workflow, actor, missed=1)
    runner.tick()
    event = TriggerEvent.system_queryset().get(trigger=trigger)
    assert seen == [event.occurs_at]
    assert WorkflowRun.system_queryset().get(trigger_event=event).input == {"value": event.occurs_at.hour}


def test_retention_prunes_settled_occurrences_without_reopening_the_window(scheduled, settings):
    actor, workflow = scheduled
    trigger = hourly(workflow, actor)
    old = timezone.now() - timedelta(days=settings.ANGEE_WORKFLOW_RETENTION_DAYS + 5)
    rows = TriggerEvent.system_queryset()
    skipped = rows.create(trigger=trigger, occurs_at=old, skipped_at=old)
    rejected = rows.create(trigger=trigger, occurs_at=old + timedelta(hours=1), evaluated_at=old, rejection="refused")
    pending = rows.create(trigger=trigger, occurs_at=old + timedelta(hours=2))
    recent = rows.create(trigger=trigger, occurs_at=timezone.now() - timedelta(days=1), skipped_at=timezone.now())
    cursor = Trigger.system_queryset().get(pk=trigger.pk).next_occurrence_at
    assert TriggerEvent.objects.prune() == 2
    assert set(rows.filter(trigger=trigger).values_list("pk", flat=True)) == {pending.pk, recent.pk}
    assert not rows.filter(pk__in=[skipped.pk, rejected.pk]).exists()
    rows.filter(pk=recent.pk).delete()
    assert Trigger.objects.record_occurrences() == 0
    assert Trigger.system_queryset().get(pk=trigger.pk).next_occurrence_at == cursor


def test_schedule_observes_no_record_saves(scheduled):
    actor, workflow = scheduled
    start = timezone.now() + timedelta(days=1)
    Trigger.objects.enable(schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=start), actor=actor)
    with pytest.raises(ValidationError, match="no record saves"):
        triggers.Schedule.validate_model(Vault)
    record = vault_for(actor, name="Quiet")
    with actor_context(actor):
        record.save()
    assert not TriggerEvent.system_queryset().exists()


def test_trigger_ledger_rows_name_a_record_or_one_occurrence(scheduled):
    actor, workflow = scheduled
    trigger = schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=DAY_ONE)
    record = vault_for(actor, name="Ledger")
    TriggerEvent.system_queryset().create(trigger=trigger, occurs_at=DAY_ONE)
    for fields in (
        {"occurs_at": DAY_ONE},
        {"occurs_at": DAY_ONE + timedelta(days=1), "record": record},
        {"occurs_at": DAY_ONE + timedelta(days=2), "skipped_at": DAY_ONE, "evaluated_at": DAY_ONE,
         "admitted_at": DAY_ONE},
        {"record": record, "skipped_at": DAY_ONE},
        {},
    ):
        with pytest.raises(IntegrityError), transaction.atomic():
            TriggerEvent.system_queryset().create(trigger=trigger, **fields)


def test_schedule_triggers_are_authored_and_read_through_the_trigger_resource(scheduled):
    actor, workflow = scheduled
    editor = create_user("schedule-editor")
    workflow.with_actor(actor).grant_record_access("editor", editor)
    schema = addon_schema(workflow_schema.schemas, "console")
    created = result_data(execute_schema(schema, """mutation($workflow: ID!, $start: DateTime!) {
      insert_trigger_one(object: {
        workflow: $workflow, source: "schedule", recurrence: "FREQ=WEEKLY;BYDAY=MO",
        recurrence_start: $start, recurrence_zone: "Europe/Brussels"
      }) {
        id display_name source_model recurrence recurrence_start recurrence_zone enabled enabled_at next_occurrence_at
      }
    }""", {"workflow": workflow.sqid, "start": DAY_ONE.isoformat()}, user=editor))["insert_trigger_one"]
    assert created["display_name"] == "Schedule: FREQ=WEEKLY;BYDAY=MO" and created["source_model"] == ""
    assert created["recurrence_zone"] == "Europe/Brussels" and created["enabled"] is False
    assert created["enabled_at"] is None and created["next_occurrence_at"] is None
    trigger = Trigger.system_queryset().get(sqid=created["id"])
    assert trigger.recurrence_start == DAY_ONE
    refused = execute_schema(schema, """mutation($workflow: ID!) {
      insert_trigger_one(object: {workflow: $workflow, source: "schedule", recurrence: "FREQ=WEEKLY"}) { id }
    }""", {"workflow": workflow.sqid}, user=editor)
    assert refused.errors and any("needs its start" in error.message for error in refused.errors)
    with transaction.atomic(), triggers.system_context(reason="test.record"):
        TriggerEvent.objects.record_occurrences(
            trigger, [DAY_ONE, DAY_ONE + timedelta(weeks=1)], supersede_before=DAY_ONE + timedelta(weeks=1),
        )
    events = result_data(execute_schema(schema, """query($trigger: String!) {
      triggerevent(where: {trigger: {_eq: $trigger}}, order_by: {occurs_at: asc}) {
        occurs_at skipped_at admitted_at record_model record_id
      }
    }""", {"trigger": trigger.sqid}, user=editor))["triggerevent"]
    assert [(event["skipped_at"] is not None, event["record_model"], event["record_id"]) for event in events] == [
        (True, None, None), (False, None, None),
    ]
