"""Tests for workflow triggers and map fan-out."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from rebac import actor_context, app_settings, system_context
from rebac.roles import grant

from angee.base.models import AngeeDataModel
from angee.graphql.events import ChangePayload
from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.graphql.subscriptions import changes
from angee.integrate.models import Bridge
from angee.workflows import engine
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
pytest_plugins = ("tests.workflows",)


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


class BackfillBridge(Bridge):
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


@pytest.fixture()
def workflow_trigger_tables(transactional_db: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Create trigger-specific concrete tables and sync workflow REBAC."""

    del transactional_db
    models = (*WORKFLOW_RUNTIME_MODELS, *TRIGGER_TEST_MODELS)
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    schemas = GraphQLSchemas(
        [
            SchemaAddon(
                {
                    "public": {
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
def item_handler(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run handlers synchronously and fail one mapped item by value."""

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
        Step.objects.create(workflow=draft, key="start", name="Start", is_entry=True)
        workflow = draft.publish()
    trigger = Trigger(
        workflow=workflow,
        kind=workflow_models.TriggerKind.EVENT,
        enabled=True,
        config={"model": UnpublishedTriggerSubject._meta.label_lower},
        event_model_label=UnpublishedTriggerSubject._meta.label_lower,
    )
    Trigger._base_manager.bulk_create([trigger])

    errors = workflow_models.check_event_trigger_change_publishers()

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
    _event_trigger(condition={"state": "ready"})
    subject = TriggerSubject.objects.create(name="first", state="ready")

    subject.name = "second"
    subject.save(update_fields=["name"])

    assert len(_runs_for_subject(subject)) == 1


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
    _event_trigger(condition={"missing_field": "ready"})

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
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    _event_trigger(condition={"state": "ready"})

    def fail_start(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("start failed")

    monkeypatch.setattr(engine, "start", fail_start)
    monkeypatch.setattr(workflow_triggers.transaction, "on_commit", lambda callback: callback())

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
        def __init__(self, cron: str, base: Any) -> None:
            del cron
            bases.append(base)

        def get_next(self, result_type: type[Any]) -> Any:
            del result_type
            return bases[-1] + timedelta(days=1)

    monkeypatch.setattr(workflow_models, "croniter", FakeCroniter)
    trigger = Trigger(config={"cron": "0 0 * * *"})

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
        Trigger.objects.filter(pk=bad.pk).update(config={"cron": "not a cron"})

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


def _event_trigger(
    *,
    condition: dict[str, Any],
    enabled: bool = True,
    config: dict[str, Any] | None = None,
    model: type[models.Model] = TriggerSubject,
) -> Trigger:
    """Create an event trigger attached to a publishable workflow lineage."""

    trigger_config = {
        "model": model._meta.label_lower,
        "condition": condition,
        **(config or {}),
    }
    with system_context(reason="test workflows event trigger"):
        draft = Workflow.objects.create(name=f"Event {condition}")
        Step.objects.create(workflow=draft, key="start", name="Start", is_entry=True)
        draft.publish()
        return Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.EVENT,
            enabled=enabled,
            config=trigger_config,
        )


def _schedule_trigger(*, config: dict[str, Any], next_fire_at: Any) -> Trigger:
    """Create a schedule trigger attached to a published workflow lineage."""

    with system_context(reason="test workflows schedule trigger"):
        draft = Workflow.objects.create(name="Schedule")
        Step.objects.create(workflow=draft, key="start", name="Start", is_entry=True)
        draft.publish()
        return Trigger.objects.create(
            workflow=draft,
            kind=workflow_models.TriggerKind.SCHEDULE,
            enabled=True,
            config=config,
            next_fire_at=next_fire_at,
        )


def _map_workflow(*, policy: dict[str, Any], items: list[str]) -> Workflow:
    """Create a workflow with one map control step and success/failure branches."""

    with system_context(reason="test workflows map definition"):
        draft = Workflow.objects.create(name=f"Map {policy}")
        entry = Step.objects.create(
            workflow=draft,
            key="entry",
            name="Entry",
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
        Step.objects.create(workflow=draft, key="item", name="Item", config={"outcome": "done"})
        passed = Step.objects.create(workflow=draft, key="passed", name="Passed", config={"outcome": "done"})
        failed = Step.objects.create(workflow=draft, key="failed", name="Failed", config={"outcome": "done"})
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
