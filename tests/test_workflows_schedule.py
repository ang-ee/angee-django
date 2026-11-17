"""Schedule triggers: the tick records occurrences, one run per occurrence, missed ones skipped."""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from rebac import actor_context

from angee.graphql.testing.documents import execute_schema, result_data
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


def enable(trigger, actor, *, since):
    """Enable through the manager, then move the enablement back to replay missed occurrences."""
    Trigger.objects.enable(trigger, actor=actor)
    Trigger.system_queryset().filter(pk=trigger.pk).update(enabled_at=since)
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
        ({"recurrence": "FREQ=DAILY"}, "first occurrence"),
        ({**daily, "model_label": "knowledge.vault"}, "admits no record"),
        ({**daily, "condition": {"name": {"_eq": "x"}}}, "admits no record"),
        ({**daily, "recurrence": "FREQ=SOMETIMES"}, "recurrence rule"),
        ({**daily, "recurrence_zone": "Europe/Brussels"}, "zone blank"),
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
    once = schedule(workflow, actor, recurrence="", recurrence_start=DAY_ONE)
    assert str(once) == "Schedule" and once.source_model == ""
    assert not Trigger.system_queryset().exclude(pk=once.pk).filter(workflow__in=[workflow, subject_workflow]).exists()


def test_schedule_enables_without_source_grants_and_tracks_its_enablement(scheduled):
    actor, workflow = scheduled
    trigger = schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=DAY_ONE)
    assert str(trigger) == "Schedule: FREQ=DAILY"
    assert Trigger.objects.enable_preview(trigger, actor=actor).grants == ()
    enabled = Trigger.objects.enable(trigger, actor=actor)
    assert enabled.enabled and enabled.granted_targets == [] and enabled.enabled_at is not None
    assert not enabled.granted_relationships(actor=actor)
    assert Trigger.objects.enable(enabled, actor=actor).enabled_at == enabled.enabled_at
    with pytest.raises(ValidationError, match="Disable the trigger"), actor_context(actor):
        enabled.recurrence = "FREQ=WEEKLY"
        enabled.save()
    disabled = Trigger.objects.disable(enabled, actor=actor)
    assert not disabled.enabled and disabled.enabled_at is None


def test_due_occurrences_open_at_enablement_and_follow_the_last_recorded(scheduled):
    actor, workflow = scheduled
    trigger = enable(
        schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=DAY_ONE),
        actor, since=DAY_ONE + timedelta(days=2, hours=3),
    )
    missed = trigger.due_occurrences(DAY_ONE + timedelta(days=5))
    assert missed == [DAY_ONE + timedelta(days=3), DAY_ONE + timedelta(days=4)]
    assert TriggerEvent.objects.record_occurrences(trigger, missed) == 2
    assert occurrence_states(trigger) == [(missed[0], True, False), (missed[1], False, False)]
    assert trigger.due_occurrences(DAY_ONE + timedelta(days=5)) == []
    following = trigger.due_occurrences(DAY_ONE + timedelta(days=5, seconds=1))
    assert following == [DAY_ONE + timedelta(days=5)]
    TriggerEvent.objects.record_occurrences(trigger, following)
    assert [skipped for _, skipped, _ in occurrence_states(trigger)] == [True, True, False]
    assert TriggerEvent.objects.record_occurrences(trigger, following) == 1
    assert TriggerEvent.system_queryset().filter(trigger=trigger).count() == 3


def test_tick_starts_one_run_for_the_latest_missed_occurrence(scheduled):
    actor, workflow = scheduled
    now = timezone.now()
    trigger = enable(
        schedule(workflow, actor, recurrence="FREQ=HOURLY", recurrence_start=now - timedelta(hours=10, minutes=30)),
        actor, since=now - timedelta(hours=3),
    )
    first = runner.tick()
    assert (first["scheduled"], first["drained"]) == (3, 1)
    assert [(skipped, admitted) for _, skipped, admitted in occurrence_states(trigger)] == [
        (True, False), (True, False), (False, True),
    ]
    latest = TriggerEvent.system_queryset().filter(trigger=trigger).latest("occurs_at")
    run = WorkflowRun.system_queryset().get(trigger_event=latest)
    assert run.origin == RunOrigin.TRIGGER and run.subject_object_id is None
    assert run.run_as_id == workflow.user_id
    assert run.request_key == f"trigger:{trigger.sqid}:{latest.occurs_at.isoformat()}"
    second = runner.tick()
    assert (second["scheduled"], second["drained"]) == (0, 0)
    # A capture repeated after a worker restart finds the occurrence already recorded and admitted.
    TriggerEvent.objects.record_occurrences(trigger, [latest.occurs_at])
    assert Trigger.objects.drain() == 0
    assert WorkflowRun.system_queryset().filter(version__workflow=workflow).count() == 1
    assert run_until(run).status == RunStatus.SUCCEEDED


def test_reenabled_schedule_runs_nothing_it_recorded_before_or_missed_while_disabled(scheduled):
    actor, workflow = scheduled
    now = timezone.now()
    trigger = enable(
        schedule(workflow, actor, recurrence="FREQ=HOURLY", recurrence_start=now - timedelta(hours=10, minutes=30)),
        actor, since=now - timedelta(hours=3),
    )
    assert Trigger.objects.record_occurrences() == 3
    Trigger.objects.disable(trigger, actor=actor)
    assert Trigger.objects.record_occurrences() == 0
    Trigger.objects.enable(trigger, actor=actor)
    assert runner.tick()["drained"] == 0
    assert all(skipped for _, skipped, _ in occurrence_states(trigger))
    assert not WorkflowRun.system_queryset().filter(version__workflow=workflow).exists()


def test_schedule_observes_no_record_saves(scheduled):
    actor, workflow = scheduled
    Trigger.objects.enable(schedule(workflow, actor, recurrence="FREQ=DAILY", recurrence_start=DAY_ONE), actor=actor)
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
        workflow: $workflow, source: "schedule", recurrence: "FREQ=WEEKLY;BYDAY=MO", recurrence_start: $start
      }) { id display_name source_model recurrence recurrence_start recurrence_zone enabled enabled_at }
    }""", {"workflow": workflow.sqid, "start": DAY_ONE.isoformat()}, user=editor))["insert_trigger_one"]
    assert created["display_name"] == "Schedule: FREQ=WEEKLY;BYDAY=MO" and created["source_model"] == ""
    assert created["recurrence_zone"] == "" and created["enabled"] is False and created["enabled_at"] is None
    trigger = Trigger.system_queryset().get(sqid=created["id"])
    assert trigger.recurrence_start == DAY_ONE
    refused = execute_schema(schema, """mutation($workflow: ID!) {
      insert_trigger_one(object: {workflow: $workflow, source: "schedule", recurrence: "FREQ=WEEKLY"}) { id }
    }""", {"workflow": workflow.sqid}, user=editor)
    assert refused.errors and any("first occurrence" in error.message for error in refused.errors)
    TriggerEvent.objects.record_occurrences(trigger, [DAY_ONE, DAY_ONE + timedelta(weeks=1)])
    events = result_data(execute_schema(schema, """query($trigger: String!) {
      triggerevent(where: {trigger: {_eq: $trigger}}, order_by: {occurs_at: asc}) {
        occurs_at skipped_at admitted_at record_model record_id
      }
    }""", {"trigger": trigger.sqid}, user=editor))["triggerevent"]
    assert [(event["skipped_at"] is not None, event["record_model"], event["record_id"]) for event in events] == [
        (True, None, None), (False, None, None),
    ]
