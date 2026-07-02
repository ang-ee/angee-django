"""Tests for workflow triggers and map fan-out."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, models
from django.utils import timezone
from rebac import app_settings, system_context
from rebac.roles import grant

from angee.graphql.schema import SCHEMA_PART_KEYS, GraphQLSchemas
from angee.integrate.models import Bridge
from angee.workflows import models as workflow_models
from angee.workflows.steps import HandlerStep, StepResult
from tests.conftest import SchemaAddon, _clear_model_tables, _create_missing_tables, execute_schema, result_data
from tests.test_workflows import Edge, Step, Trigger, Workflow
from tests.test_workflows_engine import (
    Decision,
    StepRun,
    WorkflowRun,
    advance_once,
    execute_started,
    run_to_terminal,
    step_run_for,
)

User = get_user_model()


class TriggerSubject(models.Model):
    """Concrete row saved by tests to drive the generic post-save receiver."""

    name = models.CharField(max_length=100)
    state = models.CharField(max_length=50, default="draft")

    class Meta:
        app_label = "workflows"
        db_table = "test_workflows_trigger_subject"


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


TRIGGER_TEST_MODELS = (TriggerSubject, BackfillBridge)


@pytest.fixture()
def workflow_trigger_tables(transactional_db: Any) -> Iterator[None]:
    """Create trigger-specific concrete tables and sync workflow REBAC."""

    del transactional_db
    workflow_models = (Workflow, Step, Edge, Trigger, WorkflowRun, StepRun, Decision)
    created = _create_missing_tables((*workflow_models, *TRIGGER_TEST_MODELS))
    call_command("rebac", "sync", verbosity=0)
    _clear_model_tables((*workflow_models, *TRIGGER_TEST_MODELS))
    workflow_triggers = importlib.import_module("angee.workflows.triggers")
    workflow_triggers.connect_event_trigger_receiver()
    try:
        yield
    finally:
        _clear_model_tables((*workflow_models, *TRIGGER_TEST_MODELS))
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep trigger tests synchronous by replacing queue enqueue hooks."""

    from angee.workflows import engine

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when: None)
    monkeypatch.setattr(engine, "enqueue_execute", lambda step_run_id: None)


@pytest.fixture()
def item_handler(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run handlers synchronously and fail one mapped item by value."""

    calls: list[dict[str, Any]] = []

    def run(self: HandlerStep, step_run: Any) -> StepResult:
        del self
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
    workflow = _map_workflow(policy=policy)
    run = _start_run_with_items(workflow, ["ok", "bad", "also-ok"])

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
    workflow = _map_workflow(policy={"all_must_succeed": True})
    run = _start_run_with_items(workflow, ["one", "two", "three"])

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
) -> Trigger:
    """Create an event trigger attached to a publishable workflow lineage."""

    trigger_config = {
        "model": TriggerSubject._meta.label_lower,
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


def _map_workflow(*, policy: dict[str, Any]) -> Workflow:
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
            config={"target_step": "item", "items": "run.data.items", **policy},
        )
        Step.objects.create(workflow=draft, key="item", name="Item", config={"outcome": "done"})
        passed = Step.objects.create(workflow=draft, key="passed", name="Passed", config={"outcome": "done"})
        failed = Step.objects.create(workflow=draft, key="failed", name="Failed", config={"outcome": "done"})
        Edge.objects.create(workflow=draft, source=entry, target=map_step, condition="map")
        Edge.objects.create(workflow=draft, source=map_step, target=passed, condition="succeeded")
        Edge.objects.create(workflow=draft, source=map_step, target=failed, condition="failed")
        return draft.publish()


def _start_run_with_items(workflow: Workflow, items: list[str]) -> WorkflowRun:
    """Start a workflow run and journal map input on the run data."""

    from angee.workflows import engine

    run = engine.start(workflow, subject=None, actor=None)
    with system_context(reason="test workflows map data"):
        run.data = {"items": items}
        run.save(update_fields=["data", "updated_at"])
    return run


def _runs_for_subject(subject: TriggerSubject) -> list[WorkflowRun]:
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
