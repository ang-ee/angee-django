"""Tests for workflow triggers and map fan-out."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from typing import Any

import pytest
import strawberry
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, models, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rebac import actor_context, app_settings, system_context
from rebac.errors import MissingActorError
from rebac.errors import PermissionDenied as RebacPermissionDenied
from rebac.roles import grant

from angee.base.models import AngeeDataModel, AngeeModel
from angee.graphql.events import ChangePayload
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.graphql.subscriptions import changes
from angee.integrate.models import Bridge
from angee.workflows import models as workflow_models
from angee.workflows.steps import HandlerStep, StepResult
from tests.conftest import SchemaAddon, execute_schema, result_data
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    Edge,
    Step,
    StepRun,
    Trigger,
    Workflow,
    WorkflowRun,
    advance_once,
    execute_started,
    run_to_terminal,
    start_run,
    step_run_for,
    workflow_table_setup,
)

User = get_user_model()


@pytest.fixture()
def executable_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make legacy trigger handler fixtures executable with their configured outcome."""

    def run(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        return StepResult.done(outcome=str(step_run.step.config.get("outcome", "done")))

    monkeypatch.setattr(HandlerStep, "run", run)


class TriggerSubject(models.Model):
    """Concrete row declared into the change feed for event-trigger tests."""

    name = models.CharField(max_length=100)
    state = models.CharField(max_length=50, default="draft")

    class Meta:
        app_label = "tests"
        db_table = "test_workflows_trigger_subject"


class SecuredTriggerSubject(AngeeDataModel):
    """REBAC-backed change-feed row used to pin workflow subject re-fetching."""

    sqid_prefix = "sts_"
    name = models.CharField(max_length=100)
    state = models.CharField(max_length=50, default="draft")

    class Meta:
        app_label = "chatterdemo"
        db_table = "test_workflows_secured_trigger_subject"
        rebac_resource_type = "chatterdemo/doc"
        rebac_id_attr = "sqid"


class UnpublishedTriggerSubject(models.Model):
    """Concrete row intentionally absent from the change feed."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "tests"
        db_table = "test_workflows_unpublished_trigger_subject"


class BackfillBridge(Bridge, AngeeModel):
    """Concrete bridge whose sync creates a subject row."""

    class Meta:
        app_label = "integrate"
        db_table = "test_workflows_backfill_bridge"

    def sync(self) -> int:
        """Materialize one row through the Bridge sync owner."""

        TriggerSubject.objects.create(name="backfill", state="ready")
        return 1

    def report_status(self, **kwargs: Any) -> None:
        """Match the Integration child API Bridge.record_sync calls."""


TRIGGER_TEST_MODELS = (TriggerSubject, SecuredTriggerSubject, UnpublishedTriggerSubject, BackfillBridge)


@strawberry.type
class TriggerSchemaQuery:
    """Minimal query root for the change-feed-only workflow test schema."""

    ready: bool = True


@pytest.fixture()
def workflow_trigger_tables(
    transactional_db: Any, monkeypatch: pytest.MonkeyPatch, executable_handler: None
) -> Iterator[None]:
    """Create trigger-specific concrete tables and sync workflow REBAC."""

    del transactional_db, executable_handler
    models = (*WORKFLOW_RUNTIME_MODELS, *TRIGGER_TEST_MODELS)
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    schemas = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
                        "query": (TriggerSchemaQuery,),
                        "subscription": (
                            changes(TriggerSubject, field="triggerSubjectChanged"),
                            changes(SecuredTriggerSubject, field="securedTriggerSubjectChanged"),
                        ),
                    }
                }
            )
        ]
    )
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    with workflow_table_setup(models):
        schemas.connect_change_publishers()
        workflow_triggers.connect_event_trigger_receiver()
        try:
            yield
        finally:
            for model in schemas.change_publisher_models():
                importlib.import_module("angee.graphql.publishing").disconnect_publishers(model)


@pytest.fixture()
def item_handler(monkeypatch: pytest.MonkeyPatch, executable_handler: None) -> list[dict[str, Any]]:
    """Run handlers synchronously and fail one mapped item by value."""

    del executable_handler
    calls: list[dict[str, Any]] = []

    def run(self: HandlerStep, step_run: Any, *, now: Any) -> StepResult:
        del self, now
        calls.append({"key": step_run.step.key, "input": step_run.input})
        if step_run.step.key == "item" and step_run.input.get("item") == "bad":
            raise RuntimeError("bad mapped item")
        return StepResult.done(
            output={"key": step_run.step.key, "input": step_run.input},
            outcome=str(step_run.step.config.get("outcome", "done")),
        )

    monkeypatch.setattr(HandlerStep, "run", run)
    return calls


def test_event_trigger_condition_starts_matching_saved_subject(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """A real post_save starts a run only when the trigger condition matches."""

    del workflow_trigger_tables, no_workflow_queue
    _event_trigger(condition={"state": "ready"})

    TriggerSubject.objects.create(name="skip", state="draft")
    assert _run_count() == 0

    subject = TriggerSubject.objects.create(name="fire", state="ready")

    runs = _runs_for_subject(subject)
    assert len(runs) == 1
    assert runs[0].trigger is not None


def test_event_trigger_receiver_skips_when_workflow_models_are_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A host save must not fail when concrete workflow models are not registered."""

    workflow_triggers = importlib.import_module("angee.workflows.triggers")

    def missing_model(name: str) -> object:
        raise LookupError(name)

    monkeypatch.setattr(workflow_triggers, "_model", missing_model)
    subject = TriggerSubject(name="standalone", state="ready")
    subject.pk = 1

    workflow_triggers._on_change_published(
        sender=TriggerSubject,
        payload=ChangePayload.from_instance(subject, action="create", update_fields=None),
        using="default",
    )


def test_event_trigger_requires_change_published_model(
    workflow_trigger_tables: None,
) -> None:
    """Event triggers fail loudly when their subject model is absent from changes()."""

    del workflow_trigger_tables
    with pytest.raises(ValidationError, match=r"declare changes\(\) for the model to join the change feed"):
        _event_trigger(
            condition={},
            config={"model": UnpublishedTriggerSubject._meta.label_lower},
        )


def test_event_trigger_check_rejects_persisted_non_published_model(
    workflow_trigger_tables: None,
) -> None:
    """The workflows system check reports persisted event triggers outside the feed."""

    del workflow_trigger_tables
    with system_context(reason="test invalid event trigger check setup"):
        draft = Workflow.objects.create(name="Invalid Event")
        Step.objects.create(
            workflow=draft, key="start", name="Start", is_entry=True
        )
        workflow = draft.publish()
    trigger = Trigger(
        workflow=workflow,
        kind=workflow_models.TriggerKind.EVENT,
        enabled=True,
        config={"model": UnpublishedTriggerSubject._meta.label_lower},
        event_model_label=UnpublishedTriggerSubject._meta.label_lower,
    )
    Trigger._base_manager.bulk_create([trigger])

    errors = workflow_models.check_event_trigger_publishers()

    assert any(error.id == "angee.workflows.E001" for error in errors)
    assert "declare changes() for the model to join the change feed" in "\n".join(
        error.msg for error in errors
    )


def test_event_trigger_subject_refetch_uses_system_context(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """An event trigger can resolve an AngeeManager subject without caller read scope."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    _event_trigger(condition={"state": "ready"}, model=SecuredTriggerSubject)
    with system_context(reason="test secured trigger subject seed"):
        no_actor_subject = SecuredTriggerSubject.objects.create(name="no actor", state="ready")

    workflow_triggers._on_change_published(
        sender=SecuredTriggerSubject,
        payload=ChangePayload.from_instance(no_actor_subject, action="create", update_fields=None),
    )
    assert len(_runs_for_subject(no_actor_subject)) == 1

    with system_context(reason="test secured trigger subject denied seed"):
        stranger = User.objects.create_user(username="trigger-stranger")
        denied_subject = SecuredTriggerSubject.objects.create(name="denied", state="ready")

    with actor_context(stranger):
        workflow_triggers._on_change_published(
            sender=SecuredTriggerSubject,
            payload=ChangePayload.from_instance(denied_subject, action="create", update_fields=None),
        )

    assert len(_runs_for_subject(denied_subject)) == 1


def test_disabled_event_trigger_does_not_start_run(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """Disabled triggers stay inert even when the saved row matches."""

    del workflow_trigger_tables, no_workflow_queue
    _event_trigger(condition={"state": "ready"}, enabled=False)

    TriggerSubject.objects.create(name="fire", state="ready")

    assert _run_count() == 0


def test_event_trigger_refire_dedupes_by_subject(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """Saving the same matching subject again does not create a second run."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(condition={"state": "ready"})
    assert trigger.config["admission_policy"] == "once_per_subject"
    subject = TriggerSubject.objects.create(name="first", state="ready")

    subject.name = "second"
    subject.save(update_fields=["name"])

    assert len(_runs_for_subject(subject)) == 1


def test_event_admission_policy_has_legacy_default_and_readable_summaries() -> None:
    declarations = importlib.import_module("angee.workflows.trigger_declarations")

    legacy = declarations.EventTriggerConfig.model_validate({"model": "tests.TriggerSubject"})
    each = declarations.EventTriggerConfig.model_validate(
        {"model": "tests.TriggerSubject", "admission_policy": "each_change"}
    )

    assert legacy.admission_policy == declarations.EventAdmissionPolicy.ONCE_PER_SUBJECT
    assert legacy.summary_for("Trigger subject") == "When Trigger subject changes, once per subject"
    assert each.summary_for("Trigger subject") == "When Trigger subject changes, for each matching change"


def test_event_trigger_each_change_uses_publisher_occurrence_identity(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """One publisher occurrence starts once while later changes to the same subject remain distinct."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(
        condition={"state": "ready"},
        config={"admission_policy": "each_change"},
    )
    subject = TriggerSubject.objects.create(name="first", state="draft")
    TriggerSubject.objects.filter(pk=subject.pk).update(state="ready")
    subject.refresh_from_db()
    now = timezone.now()

    first = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="change-1",
        timestamp=now,
    )
    duplicate = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="change-1",
        timestamp=now,
    )
    second = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="change-2",
        timestamp=now + timedelta(seconds=1),
    )

    assert first is not None
    assert duplicate is not None
    assert duplicate.pk == first.pk
    assert second is not None
    assert second.pk != first.pk
    with system_context(reason="inspect event occurrence runs"):
        occurrences = list(WorkflowRun.objects.order_by("pk").values_list("occurrence_id", flat=True))
    assert occurrences == ["change-1", "change-2"]
    trigger.refresh_from_db()
    assert trigger.hourly_fire_count == 2


def test_event_trigger_each_change_declines_unidentified_legacy_payload(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """Each-change rules never fabricate occurrence identity for a legacy publisher payload."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(condition={"state": "ready"}, config={"admission_policy": "each_change"})
    subject = TriggerSubject.objects.create(name="legacy", state="draft")
    subject.state = "ready"

    assert Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id=None,
        timestamp=timezone.now(),
    ) is None
    assert _run_count() == 0


def test_event_duplicate_after_publication_replacement_returns_original_pinned_run(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """A retried occurrence resolves to its original run after a newer publication exists."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(
        condition={"state": "ready"},
        config={"admission_policy": "each_change"},
    )
    subject = TriggerSubject.objects.create(name="publication", state="draft")
    TriggerSubject.objects.filter(pk=subject.pk).update(state="ready")
    subject.refresh_from_db()
    first = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="stable-change",
        timestamp=timezone.now(),
    )
    assert first is not None
    with system_context(reason="replace workflow publication after event admission"):
        head = Workflow.objects.get(pk=trigger.workflow_id)
        entry = head.steps.get(is_entry=True)
        entry.name = "New entry"
        entry.save(update_fields={"name", "updated_at"})
        head.publish()

    duplicate = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="stable-change",
        timestamp=timezone.now(),
    )

    assert duplicate is not None
    assert duplicate.pk == first.pk
    assert duplicate.workflow_id == first.workflow_id
    assert _run_count() == 1


def test_event_failed_admission_rolls_back_counters_and_remains_retryable(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed atomic run creation consumes neither the occurrence nor rate-limit facts."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(
        condition={"state": "ready"},
        config={"admission_policy": "each_change"},
    )
    subject = TriggerSubject.objects.create(name="retryable", state="draft")
    TriggerSubject.objects.filter(pk=subject.pk).update(state="ready")
    subject.refresh_from_db()
    ContentType.objects.get_for_model(subject, for_concrete_model=False)
    original = WorkflowRun.objects._start_locked

    def fail_start(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        raise RuntimeError("admission failed")

    monkeypatch.setattr(WorkflowRun.objects, "_start_locked", fail_start)
    with pytest.raises(RuntimeError, match="admission failed"):
        Trigger.objects.start_event(
            trigger.pk,
            subject=subject,
            occurrence_id="retryable-change",
            timestamp=timezone.now(),
        )
    trigger.refresh_from_db()
    assert trigger.hourly_fire_count == 0
    assert trigger.last_fire_at is None
    monkeypatch.setattr(WorkflowRun.objects, "_start_locked", original)

    admitted = Trigger.objects.start_event(
        trigger.pk,
        subject=subject,
        occurrence_id="retryable-change",
        timestamp=timezone.now(),
    )
    assert admitted is not None


def test_event_trigger_cooldown_and_hourly_cap_are_locked_facts(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cooldown and hourly caps skip same-window fires on the Trigger row."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    now = timezone.now()
    monkeypatch.setattr(workflow_triggers.timezone, "now", lambda: now)
    _event_trigger(
        condition={"state": "cooldown"},
        config={"cooldown_seconds": 60, "hourly_cap": 10},
    )
    _event_trigger(
        condition={"state": "capped"},
        config={"cooldown_seconds": 0, "hourly_cap": 1},
    )

    TriggerSubject.objects.create(name="cooldown-1", state="cooldown")
    TriggerSubject.objects.create(name="cooldown-2", state="cooldown")
    TriggerSubject.objects.create(name="capped-1", state="capped")
    TriggerSubject.objects.create(name="capped-2", state="capped")

    assert _run_count() == 2


def test_event_trigger_bad_condition_is_logged_and_skipped(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One invalid event condition never breaks the host model save."""

    del workflow_trigger_tables, no_workflow_queue
    trigger = _event_trigger(condition={"missing_field": "ready"}, enabled=False)
    with system_context(reason="test historical invalid event trigger"):
        models.QuerySet.update(Trigger.objects.filter(pk=trigger.pk), enabled=True)

    TriggerSubject.objects.create(name="invalid-condition", state="ready")

    assert _run_count() == 0
    assert "condition" in caplog.text


def test_event_trigger_start_error_is_logged_and_does_not_break_save(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Engine start failures are isolated to the trigger dispatch path."""

    del workflow_trigger_tables, no_workflow_queue
    importlib.import_module("angee.workflows.triggers")
    _event_trigger(condition={"state": "ready"})

    def fail_start(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("start failed")

    monkeypatch.setattr(Trigger.objects, "start_event", fail_start)

    TriggerSubject.objects.create(name="start-error", state="ready")

    assert "start failed" in caplog.text


def test_bridge_sync_marked_saves_are_skipped_but_live_saves_fire(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """Rows created during Bridge.run_sync are backfill saves; live saves still fire."""

    del workflow_trigger_tables, no_workflow_queue
    _event_trigger(condition={"state": "ready"})
    now = timezone.now()
    with system_context(reason="test workflows trigger bridge sync"):
        bridge = BackfillBridge.objects.create(poll_interval=60)
        bridge.run_sync(now=now)

    assert _run_count() == 0

    TriggerSubject.objects.create(name="live", state="ready")

    assert _run_count() == 1


def test_schedule_trigger_fires_when_due_and_computes_next_fire(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """The due scan uses an injected timestamp and advances next_fire_at."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    now = timezone.now().replace(microsecond=0)
    trigger = _schedule_trigger(config={"interval_seconds": 3600}, next_fire_at=now)

    assert workflow_triggers.run_due_schedule_triggers(now=now) == {"triggers": 1, "fired": 1, "skipped": 0}
    trigger.refresh_from_db()

    assert _run_count() == 1
    assert trigger.next_fire_at == now + timedelta(hours=1)

    assert workflow_triggers.run_due_schedule_triggers(now=now + timedelta(minutes=30)) == {
        "triggers": 0,
        "fired": 0,
        "skipped": 0,
    }
    assert workflow_triggers.run_due_schedule_triggers(now=now + timedelta(hours=1)) == {
        "triggers": 1,
        "fired": 1,
        "skipped": 0,
    }
    assert _run_count() == 2


def test_schedule_start_failure_rolls_back_due_claim(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed pinned start leaves its schedule occurrence durably retryable."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    now = timezone.now().replace(microsecond=0)
    trigger = _schedule_trigger(config={"interval_seconds": 3600}, next_fire_at=now)
    with system_context(reason="retire schedule publication"):
        published = Workflow.objects.current_published_for(trigger.workflow)
        assert published is not None
        published.archive()

    assert workflow_triggers.run_due_schedule_triggers(now=now) == {
        "triggers": 1,
        "fired": 0,
        "skipped": 1,
    }
    trigger.refresh_from_db()
    assert trigger.next_fire_at == now
    assert trigger.last_fire_at is None
    assert trigger.hourly_fire_count == 0
    assert _run_count() == 0
    assert "failed to start workflow" in caplog.text


def test_schedule_trigger_primes_missing_next_fire_with_injected_timestamp(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
) -> None:
    """A schedule trigger with no indexed due time is primed by the due scan."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    now = timezone.now().replace(microsecond=0)
    trigger = _schedule_trigger(config={"interval_seconds": 3600}, next_fire_at=None)

    assert workflow_triggers.run_due_schedule_triggers(now=now) == {"triggers": 0, "fired": 0, "skipped": 0}
    trigger.refresh_from_db()

    assert _run_count() == 0
    assert trigger.next_fire_at == now + timedelta(hours=1)


def test_schedule_trigger_validation_requires_cron_xor_interval(
    workflow_trigger_tables: None,
) -> None:
    """Schedule triggers declare exactly one valid scheduling primitive."""

    del workflow_trigger_tables
    with pytest.raises(ValidationError, match="cron or interval"):
        _schedule_trigger(config={}, next_fire_at=None)
    with pytest.raises(ValidationError, match="cron or interval"):
        _schedule_trigger(config={"cron": "* * * * *", "interval_seconds": 60}, next_fire_at=None)
    with pytest.raises(ValidationError, match="cron"):
        _schedule_trigger(config={"cron": "not a cron"}, next_fire_at=None)


def test_schedule_cron_catch_up_uses_one_base_at_max_after_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cron catch-up asks croniter once from max(after, now), not once per missed tick."""

    bases: list[Any] = []
    after = timezone.now().replace(microsecond=0)
    now = after + timedelta(days=30)

    class FakeCroniter:
        def __init__(self, cron: str, base: Any = None) -> None:
            del cron
            if base is not None:
                bases.append(base)

        def get_next(self, result_type: type[Any]) -> Any:
            del result_type
            return bases[-1] + timedelta(days=1)

    declarations = importlib.import_module("angee.workflows.trigger_declarations")
    monkeypatch.setattr(declarations, "croniter", FakeCroniter)
    trigger = Trigger(kind=workflow_models.TriggerKind.SCHEDULE, config={"cron": "0 0 * * *"})

    assert trigger.compute_next_fire_at(after=after, now=now) == now + timedelta(days=1)
    assert bases == [now]


def test_bad_schedule_row_is_logged_and_does_not_stop_scan(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Legacy bad schedule config skips that row while valid schedules keep firing."""

    del workflow_trigger_tables, no_workflow_queue
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    now = timezone.now().replace(microsecond=0)
    bad = _schedule_trigger(config={"interval_seconds": 3600}, next_fire_at=now)
    good = _schedule_trigger(config={"interval_seconds": 3600}, next_fire_at=now)
    with system_context(reason="test workflows invalid schedule row"):
        models.QuerySet.update(Trigger.objects.filter(pk=bad.pk), config={"cron": "not a cron"})

    assert workflow_triggers.run_due_schedule_triggers(now=now) == {"triggers": 2, "fired": 1, "skipped": 1}
    good.refresh_from_db()
    assert good.next_fire_at == now + timedelta(hours=1)
    assert "schedule trigger" in caplog.text


@pytest.mark.parametrize(
    ("policy", "expected_outcome", "expected_branch"),
    [
        ({"min_success_ratio": 0.5}, "succeeded", "passed"),
        ({"all_must_succeed": True}, "failed", "failed"),
    ],
)
def test_map_aggregates_child_outcomes_and_routes_by_policy(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    item_handler: list[dict[str, Any]],
    policy: dict[str, Any],
    expected_outcome: str,
    expected_branch: str,
) -> None:
    """A map step fans out one target step and routes on aggregate policy."""

    del workflow_trigger_tables, no_workflow_queue, item_handler
    workflow = _map_workflow(policy=policy, items=["ok", "bad", "also-ok"])
    run = start_run(workflow)

    run_to_terminal(run)
    run.refresh_from_db()

    assert run.status == workflow_models.RunStatus.SUCCEEDED
    map_row = step_run_for(run, "map")
    assert map_row.status == workflow_models.StepRunStatus.SUCCEEDED
    assert map_row.outcome == expected_outcome
    assert map_row.output["total"] == 3
    assert map_row.output["successes"] == 2
    assert map_row.output["failures"] == 1
    assert step_run_for(run, expected_branch).status == workflow_models.StepRunStatus.SUCCEEDED
    with system_context(reason="test workflows map children"):
        item_rows = list(StepRun.objects.filter(run=run, step__key="item").order_by("map_index"))
    assert [row.map_index for row in item_rows] == [0, 1, 2]


def test_map_replay_does_not_duplicate_sibling_step_runs(
    workflow_trigger_tables: None,
    no_workflow_queue: None,
    item_handler: list[dict[str, Any]],
) -> None:
    """Replaying advance while a map is waiting reuses existing indexed siblings."""

    del workflow_trigger_tables, no_workflow_queue, item_handler
    workflow = _map_workflow(policy={"all_must_succeed": True}, items=["one", "two", "three"])
    run = start_run(workflow)

    advance_once(run)
    execute_started(run)
    advance_once(run)
    advance_once(run)

    with system_context(reason="test workflows map replay"):
        item_rows = list(StepRun.objects.filter(run=run, step__key="item").order_by("map_index"))
        map_row = StepRun.objects.get(run=run, step__key="map")
    assert [row.map_index for row in item_rows] == [0, 1, 2]
    assert map_row.resume_state["map"]["items"] == ["one", "two", "three"]


def test_console_can_enable_and_disable_triggers(
    workflow_trigger_tables: None,
) -> None:
    """The console exposes explicit trigger enable/disable actions."""

    del workflow_trigger_tables
    workflows_schema = importlib.import_module("angee.workflows.schema")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")
    trigger = _event_trigger(condition={"state": "ready"}, enabled=False)
    admin = _platform_admin("workflow-trigger-admin")

    enable = """
      mutation Enable($id: ID!) {
        enable_workflow_trigger(trigger: $id) { ok message }
      }
    """
    disable = """
      mutation Disable($id: ID!) {
        disable_workflow_trigger(trigger: $id) { ok message }
      }
    """

    enabled = result_data(execute_schema(schema, enable, {"id": trigger.sqid}, user=admin))["enable_workflow_trigger"]
    trigger.refresh_from_db()
    assert enabled["ok"] is True
    assert trigger.enabled is True

    disabled = result_data(execute_schema(schema, disable, {"id": trigger.sqid}, user=admin))[
        "disable_workflow_trigger"
    ]
    trigger.refresh_from_db()
    assert disabled["ok"] is True
    assert trigger.enabled is False


def test_schedule_preview_projects_invalid_drafts_as_typed_data(
    workflow_trigger_tables: None,
) -> None:
    """Expected authoring errors stay in the preview payload, not GraphQL errors."""

    del workflow_trigger_tables
    workflows_schema = importlib.import_module("angee.workflows.schema")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")
    admin = _platform_admin("workflow-trigger-preview-admin")
    query = """
      query Preview($config: JSON!, $count: Int!) {
        workflow_schedule_preview(config: $config, count: $count) {
          timezone occurrences errors
        }
      }
    """

    result = execute_schema(schema, query, {"config": {"interval_seconds": ""}, "count": 3}, user=admin)

    assert result.errors is None
    assert result_data(result)["workflow_schedule_preview"] == {
        "timezone": "UTC",
        "occurrences": [],
        "errors": ["Value error, Schedule triggers require cron or interval_seconds, but not both."],
    }


def test_event_condition_draft_preserves_json_scalar_presence_and_invalid_opaque(
    workflow_trigger_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The authored GraphQL projection carries false, zero and null without coercion."""

    del workflow_trigger_tables
    workflows_schema = importlib.import_module("angee.workflows.schema")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")
    class Discovery:
        def change_publisher_models(self) -> tuple[type[models.Model], ...]:
            return (TriggerSubject,)

        def model_readable_fields(self, model: type[models.Model]) -> frozenset[str]:
            assert model is TriggerSubject
            return frozenset({"name", "state"})

    monkeypatch.setattr(workflows_schema.GraphQLSchemas, "from_discovery", lambda: Discovery())
    admin = _platform_admin("workflow-trigger-condition-admin")
    catalogue_query = """
      query EventConditionCatalogue($model: String!, $condition: JSON) {
        workflow_event_condition_draft(model: $model, condition: $condition) {
          fields { name scalar lookups { name key label value_schema } }
        }
      }
    """
    publishers_query = """
      query EventPublishers { workflow_trigger_publishers { model } }
    """
    publishers = result_data(execute_schema(schema, publishers_query, user=admin))["workflow_trigger_publishers"]
    fields_by_scalar: dict[str, tuple[str, str]] = {}
    for publisher in publishers:
        model = publisher["model"]
        result = execute_schema(schema, catalogue_query, {"model": model, "condition": {}}, user=admin)
        assert result.errors is None
        for field in result_data(result)["workflow_event_condition_draft"]["fields"]:
            fields_by_scalar.setdefault(field["scalar"], (model, field["name"]))
            assert all(
                lookup["key"] and lookup["label"] and lookup["value_schema"]
                for lookup in field["lookups"]
            )

    draft_query = """
      query EventConditionDraft($model: String!, $condition: JSON) {
        workflow_event_condition_draft(model: $model, condition: $condition) {
          clauses { field lookup value source_key }
          opaque condition errors
        }
      }
    """
    model, field = next(iter(fields_by_scalar.values()))
    condition = {field: None, "opaque_false": False, "opaque_zero": 0}
    result = execute_schema(schema, draft_query, {"model": model, "condition": condition}, user=admin)
    assert result.errors is None
    draft = result_data(result)["workflow_event_condition_draft"]
    assert draft["clauses"][0]["value"] is None
    assert draft["opaque"] == {"opaque_false": False, "opaque_zero": 0}
    assert draft["condition"] == condition

    malformed = execute_schema(schema, draft_query, {"model": model, "condition": None}, user=admin)
    assert malformed.errors is None
    assert result_data(malformed)["workflow_event_condition_draft"]["errors"] == [
        "Condition must be a JSON object."
    ]

    encode_query = """
      query InvalidOpaque($model: String!, $condition: JSON, $opaque: JSON) {
        workflow_event_condition_draft(model: $model, condition: $condition, clauses: [], opaque: $opaque) {
          condition errors
        }
      }
    """
    original = {field: None}
    result = execute_schema(schema, encode_query, {"model": model, "condition": original, "opaque": []}, user=admin)
    assert result.errors is None
    assert result_data(result)["workflow_event_condition_draft"] == {
        "condition": original,
        "errors": ["Opaque condition entries must be a JSON object."],
    }


def test_trigger_list_projects_summary_and_blocker_without_per_row_queries(
    workflow_trigger_tables: None,
) -> None:
    """Trigger authoring projections load publication state once for the collection."""

    del workflow_trigger_tables
    workflows_schema = importlib.import_module("angee.workflows.schema")
    schema = GraphQLSchemas(
        [
            SchemaAddon(
                {"console": {key: tuple(workflows_schema.schemas["console"].get(key, ())) for key in SCHEMA_PART_KEYS}}
            )
        ]
    ).build("console")
    with system_context(reason="test trigger list projection"):
        draft = Workflow.objects.create(name="Trigger list")
        Step.objects.create(workflow=draft, key="start", name="Start", is_entry=True)
        draft.publish()
        Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.EVENT,
            config={"model": TriggerSubject._meta.label_lower, "condition": {}, "admission_policy": "each_change"},
        )
        Trigger.objects.create(workflow=draft, kind=workflow_models.TriggerKind.MANUAL, config={})
        Trigger.objects.create(workflow=draft, kind=workflow_models.TriggerKind.MANUAL, config={})
    admin = _platform_admin("workflow-trigger-list-admin")
    query = """
      query TriggerList {
        workflow_triggers { id summary activation_blocker last_fire_at }
      }
    """

    with CaptureQueriesContext(connection) as queries:
        rows = result_data(execute_schema(schema, query, user=admin))["workflow_triggers"]

    assert [row["summary"] for row in rows] == [
        "When trigger subject changes, for each matching change",
        "Manual start",
        "Manual start",
    ]
    trigger_selects = [
        item["sql"] for item in queries.captured_queries
        if Trigger._meta.db_table in item["sql"] and item["sql"].lstrip().upper().startswith("SELECT")
    ]
    assert len(trigger_selects) == 1
    domain_selects = [
        item["sql"]
        for item in queries.captured_queries
        if item["sql"].lstrip().upper().startswith("SELECT")
        and (Trigger._meta.db_table in item["sql"] or Workflow._meta.db_table in item["sql"])
    ]
    assert domain_selects == trigger_selects


def _event_trigger(
    *,
    condition: dict[str, Any],
    enabled: bool = True,
    config: dict[str, Any] | None = None,
    model: type[models.Model] = TriggerSubject,
) -> Trigger:
    """Create an event trigger attached to a publishable workflow lineage."""

    ContentType.objects.clear_cache()

    trigger_config = {
        "model": model._meta.label_lower,
        "condition": condition,
        **(config or {}),
    }
    with system_context(reason="test workflows event trigger"):
        draft = Workflow.objects.create(name=f"Event {condition}")
        Step.objects.create(
            workflow=draft, key="start", name="Start", is_entry=True
        )
        draft.publish()
        trigger = Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.EVENT,
            config=trigger_config,
        )
        if enabled:
            trigger.enable()
        return trigger


def _schedule_trigger(*, config: dict[str, Any], next_fire_at: Any) -> Trigger:
    """Create a schedule trigger attached to a published workflow lineage."""

    with system_context(reason="test workflows schedule trigger"):
        draft = Workflow.objects.create(name="Schedule")
        Step.objects.create(
            workflow=draft, key="start", name="Start", is_entry=True
        )
        draft.publish()
        trigger = Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.SCHEDULE,
            config=config,
            next_fire_at=next_fire_at,
        )
        trigger.enable()
        models.QuerySet.update(Trigger.objects.filter(pk=trigger.pk), next_fire_at=next_fire_at)
        trigger.next_fire_at = next_fire_at
        return trigger


def test_trigger_creation_is_disabled_but_still_validates_rule_shape(
    workflow_trigger_tables: None,
) -> None:
    """Every creation path disables rows without accepting malformed authored rules."""

    del workflow_trigger_tables
    with system_context(reason="test trigger creation contract"):
        draft = Workflow.objects.create(name="Trigger creation")
        trigger = Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.SCHEDULE,
            enabled=True,
            config={"interval_seconds": 60},
        )
        assert trigger.enabled is False
        with pytest.raises(ValidationError, match="cron or interval"):
            Trigger.objects.create(
                workflow=draft,
                kind=workflow_models.TriggerKind.SCHEDULE,
                config={},
            )


def test_invalid_legacy_trigger_can_only_bypass_validation_to_disable(
    workflow_trigger_tables: None,
) -> None:
    """Operational disable repairs actual state without opening a general validation bypass."""

    del workflow_trigger_tables
    trigger = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=timezone.now())
    with system_context(reason="test invalid legacy trigger"):
        models.QuerySet.update(Trigger.objects.filter(pk=trigger.pk), config={"cron": "invalid"})
        trigger.refresh_from_db()
        trigger.disable()
        trigger.refresh_from_db()
        assert trigger.enabled is False
        trigger.config = {"cron": "invalid"}
        with pytest.raises(ValidationError, match="cron"):
            trigger.save(update_fields={"config", "updated_at"})


def test_trigger_activation_preserves_caller_authorization_and_rejects_stale_lineage(
    workflow_trigger_tables: None,
) -> None:
    """Direct actions require record access and cannot follow a stale instance to another head."""

    del workflow_trigger_tables
    trigger = _event_trigger(condition={}, enabled=False)
    with pytest.raises((MissingActorError, RebacPermissionDenied)):
        trigger.enable()
    with pytest.raises((MissingActorError, RebacPermissionDenied)):
        Trigger.objects.set_enabled(trigger, enabled=True)

    with system_context(reason="test stale trigger activation"):
        replacement = Workflow.objects.create(name="Replacement")
        Step.objects.create(workflow=replacement, key="start", name="Start", is_entry=True)
        replacement.publish()
        models.QuerySet.update(Trigger.objects.filter(pk=trigger.pk), workflow_id=replacement.pk)
        with pytest.raises(ValidationError, match="lineage changed"):
            trigger.enable()


@pytest.mark.parametrize(
    "condition",
    (
        {"missing__exact": "value"},
        {"name__year": 2026},
        {"id__in": 1},
    ),
)
def test_event_trigger_enable_compiles_condition_without_running_it(
    workflow_trigger_tables: None,
    condition: dict[str, Any],
) -> None:
    """Invalid fields, lookups and values are rejected before event delivery."""

    del workflow_trigger_tables
    trigger = _event_trigger(condition=condition, enabled=False)
    with system_context(reason="test invalid event condition activation"):
        with pytest.raises(ValidationError, match="condition is invalid"):
            trigger.enable()


def test_event_trigger_enable_accepts_empty_in_condition(
    workflow_trigger_tables: None,
) -> None:
    """A provably empty lookup is valid and enables without querying subjects."""

    del workflow_trigger_tables
    trigger = _event_trigger(condition={"id__in": []}, enabled=False)
    with system_context(reason="test empty event condition activation"):
        trigger.enable()
    trigger.refresh_from_db()
    assert trigger.enabled is True


def test_trigger_save_merges_partial_rule_fields_and_rejects_stale_activation(
    workflow_trigger_tables: None,
) -> None:
    """Validation uses the locked effective row and a stale full save cannot re-enable it."""

    del workflow_trigger_tables
    trigger = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=timezone.now())
    stale = Trigger.objects.sudo(reason="test stale trigger").get(pk=trigger.pk)
    with system_context(reason="test concurrent trigger edit"):
        models.QuerySet.update(
            Trigger.objects.filter(pk=trigger.pk),
            kind=workflow_models.TriggerKind.MANUAL,
            enabled=False,
        )
        stale.config = {"interval_seconds": 120}
        with pytest.raises(ValidationError, match="enable or disable"):
            stale.save()

        current = Trigger.objects.get(pk=trigger.pk)
        current.config = {"legacy": True}
        current.save(update_fields={"config", "updated_at"})
        current.refresh_from_db()
        assert current.kind == workflow_models.TriggerKind.MANUAL
        assert current.config == {"legacy": True}


def test_rule_saves_preserve_operational_state_and_only_cadence_reschedules(
    workflow_trigger_tables: None,
) -> None:
    """Rule authoring cannot overwrite counters or move a schedule for unrelated config."""

    del workflow_trigger_tables
    due_at = timezone.now() + timedelta(hours=1)
    trigger = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=due_at)
    stale = Trigger.objects.sudo(reason="test stale operational trigger").get(pk=trigger.pk)
    fired_at = timezone.now()
    with system_context(reason="test trigger operational state"):
        models.QuerySet.update(
            Trigger.objects.filter(pk=trigger.pk),
            last_fire_at=fired_at,
            hourly_window_started_at=fired_at,
            hourly_fire_count=1,
        )
        stale.config = {"interval_seconds": 60, "cooldown_seconds": 10, "opaque": True}
        stale.save()
        stale.refresh_from_db()
        assert stale.last_fire_at == fired_at
        assert stale.hourly_fire_count == 1
        assert stale.next_fire_at == due_at
        stale.config = {"interval_seconds": "60"}
        stale.save(update_fields={"config", "updated_at"})
        stale.refresh_from_db()
        assert stale.next_fire_at == due_at
        stale.next_fire_at = due_at - timedelta(minutes=5)
        with pytest.raises(RuntimeError, match="owning manager"):
            stale.save(update_fields={"next_fire_at", "updated_at"})

        stale.disable()
        disabled_due_at = stale.next_fire_at
        stale.config = {"interval_seconds": 120}
        stale.save(update_fields={"config", "updated_at"})
        stale.refresh_from_db()
        assert stale.enabled is False
        assert stale.next_fire_at == disabled_due_at


def test_operational_fields_only_change_through_locked_manager_transitions(
    workflow_trigger_tables: None,
) -> None:
    """Collection writes are closed and a stale public fire derives from the locked row."""

    del workflow_trigger_tables
    trigger = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=timezone.now())
    fired_at = timezone.now()
    stale = Trigger.objects.sudo(reason="test stale fire caller").get(pk=trigger.pk)
    with system_context(reason="test trigger operational guards"):
        with pytest.raises(TypeError, match="QuerySet.update"):
            Trigger.objects.filter(pk=trigger.pk).update(hourly_fire_count=99)
        with pytest.raises(TypeError, match="bulk_update"):
            Trigger.objects.bulk_update([trigger], ["event_model_label"])

        models.QuerySet.update(
            Trigger.objects.filter(pk=trigger.pk),
            hourly_window_started_at=fired_at,
            hourly_fire_count=4,
        )
        with transaction.atomic():
            stale.record_fire(timestamp=fired_at + timedelta(seconds=1))

        trigger.refresh_from_db()
        assert trigger.hourly_window_started_at == fired_at
        assert trigger.hourly_fire_count == 5
        assert trigger.last_fire_at == fired_at + timedelta(seconds=1)


@pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL trigger serialization contract")
def test_trigger_edit_and_activation_serialize_on_lineage_then_trigger(
    workflow_trigger_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An activation racing a rule edit validates and schedules the committed rule."""

    del workflow_trigger_tables
    trigger = _schedule_trigger(config={"interval_seconds": 60}, next_fire_at=timezone.now())
    with system_context(reason="prepare trigger activation race"):
        trigger.disable()

    edit_has_locks = Event()
    release_edit = Event()
    original_full_clean = Trigger.full_clean

    def pause_locked_edit(instance: Trigger, *args: Any, **kwargs: Any) -> None:
        if instance.pk == trigger.pk and instance.config == {"interval_seconds": 120} and not instance.enabled:
            edit_has_locks.set()
            assert release_edit.wait(timeout=10)
        original_full_clean(instance, *args, **kwargs)

    monkeypatch.setattr(Trigger, "full_clean", pause_locked_edit)

    def edit_rule() -> None:
        close_old_connections()
        try:
            with system_context(reason="concurrent trigger edit"):
                row = Trigger.objects.get(pk=trigger.pk)
                row.config = {"interval_seconds": 120}
                row.save(update_fields={"config", "updated_at"})
        finally:
            connections.close_all()

    def enable_rule() -> None:
        close_old_connections()
        try:
            with system_context(reason="concurrent trigger activation"):
                Trigger.objects.get(pk=trigger.pk).enable()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        edited = pool.submit(edit_rule)
        assert edit_has_locks.wait(timeout=10)
        enabled = pool.submit(enable_rule)
        release_edit.set()
        edited.result(timeout=10)
        enabled.result(timeout=10)

    with system_context(reason="verify trigger activation race"):
        trigger.refresh_from_db()
    assert trigger.enabled is True
    assert trigger.config == {"interval_seconds": 120}
    assert trigger.next_fire_at is not None
    assert trigger.next_fire_at > timezone.now() + timedelta(seconds=100)


def test_event_index_uses_the_validated_alias_precedence(
    workflow_trigger_tables: None,
) -> None:
    """The indexed target cannot disagree with declaration alias normalization."""

    del workflow_trigger_tables
    with system_context(reason="test trigger index projection"):
        draft = Workflow.objects.create(name="Trigger alias")
        trigger = Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.EVENT,
            config={"model": "   ", "model_label": TriggerSubject._meta.label_lower},
        )
    assert trigger.event_model_label == TriggerSubject._meta.label_lower


def _map_workflow(*, policy: dict[str, Any], items: list[str]) -> Workflow:
    """Create a workflow with one map control step and success/failure branches."""

    with system_context(reason="test workflows map definition"):
        draft = Workflow.objects.create(name=f"Map {policy}")
        entry = Step.objects.create(
            workflow=draft,
            key="entry",
            name="Entry",
            step_class="handler",
            is_entry=True,
            config={"outcome": "map"},
        )
        map_step = Step.objects.create(
            workflow=draft,
            key="map",
            name="Map",
            step_class="map",
            config={"target_step": "item", "items": items, **policy},
        )
        Step.objects.create(
            workflow=draft, key="item", name="Item", step_class="handler", config={"outcome": "done"}
        )
        passed = Step.objects.create(
            workflow=draft, key="passed", name="Passed", step_class="handler", config={"outcome": "done"}
        )
        failed = Step.objects.create(
            workflow=draft, key="failed", name="Failed", step_class="handler", config={"outcome": "done"}
        )
        Edge.objects.create(workflow=draft, source=entry, target=map_step, condition="map")
        Edge.objects.create(workflow=draft, source=map_step, target=passed, condition="succeeded")
        Edge.objects.create(workflow=draft, source=map_step, target=failed, condition="failed")
        return draft.publish()


def _runs_for_subject(subject: models.Model) -> list[WorkflowRun]:
    """Return workflow runs started for ``subject``."""

    with system_context(reason="test workflows trigger runs"):
        return list(
            WorkflowRun.objects.filter(
                subject_object_id=subject.pk,
                subject_content_type__app_label=subject._meta.app_label,
                subject_content_type__model=subject._meta.model_name,
            )
            .select_related("trigger")
            .order_by("pk")
        )


def _run_count() -> int:
    """Return the workflow-run count under system context."""

    with system_context(reason="test workflows trigger run count"):
        return WorkflowRun.objects.count()


def _platform_admin(username: str) -> Any:
    """Create a superuser holding the platform-admin role tuple."""

    admin = User.objects.create_superuser(username=username, email=f"{username}@example.com", password="admin")
    grant(actor=admin, role=app_settings.REBAC_UNIVERSAL_ADMIN_ROLE)
    return admin
