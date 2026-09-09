"""Workflow Map journal capacity regressions."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import models
from rebac import system_context

from angee.workflows import engine
from angee.workflows.models import RunStatus, StepRunStatus
from tests.workflows import StepRun, Workflow, WorkflowRun, start_run, workflow_with_steps


def _map_workflow(*, max_steps: int, items: Any = None, two_maps: bool = False) -> Any:
    map_items = ["one", "two"] if items is None else items
    steps = [
        {
            "key": "map_one",
            "step_class": "map",
            "config": {"target_step": "body_one", "items": map_items},
        },
        {"key": "body_one", "step_class": "agent_session"},
    ]
    if two_maps:
        steps.extend(
            [
                {
                    "key": "map_two",
                    "step_class": "map",
                    "config": {"target_step": "body_two", "items": ["other"]},
                    "is_entry": False,
                },
                {"key": "body_two", "step_class": "agent_session", "is_entry": False},
            ]
        )
    needs_historical_budget = two_maps or (
        isinstance(map_items, list) and len(map_items) + 1 > max_steps
    )
    workflow = workflow_with_steps(
        name="Map capacity",
        # Runtime capacity remains authoritative for dynamic and historical
        # definitions whose budget predates readiness validation.
        max_steps=100 if needs_historical_budget else max_steps,
        steps=tuple(steps),
        edges=(("map_one", "map_two", "succeeded"),) if two_maps else (),
    )
    if needs_historical_budget:
        models.QuerySet.update(Workflow.objects.filter(pk=workflow.pk), max_steps=max_steps)
        workflow.refresh_from_db()
    return workflow


@pytest.mark.parametrize("items", [["one", "two"], "input.items"])
def test_map_overflow_fails_before_allocating_any_children(
    workflow_engine_tables: None,
    no_workflow_queue: None,
    items: Any,
) -> None:
    """Literal and runtime-resolved collections cannot over-allocate the journal."""

    del workflow_engine_tables, no_workflow_queue
    run = start_run(_map_workflow(max_steps=2, items=items))
    if isinstance(items, str):
        with system_context(reason="test dynamic Map input"):
            row = StepRun.objects.get(run=run, step__key="map_one")
            row.input = {"items": ["one", "two"]}
            row.save(update_fields=["input", "updated_at"])

    assert engine.advance(run.pk) == {"claimed": 0}

    run.refresh_from_db()
    with system_context(reason="test Map overflow journal"):
        map_row = StepRun.objects.get(run=run, step__key="map_one")
        has_children = StepRun.objects.filter(run=run, map_index__gte=0).exists()
    assert run.status == RunStatus.FAILED
    assert run.steps_taken == 0
    assert run.error == "Workflow exceeded max_steps=2."
    assert map_row.status == StepRunStatus.SCHEDULED
    assert map_row.resume_state == {}
    assert not has_children


def test_malformed_maps_cannot_bypass_the_parent_capacity_boundary(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Config failure outcomes still consume bounded Map executions."""

    del workflow_engine_tables, no_workflow_queue
    workflow = _map_workflow(max_steps=1, items="input.missing", two_maps=True)
    run = start_run(workflow)
    with system_context(reason="test malformed Map capacity"):
        map_two = workflow.steps.get(key="map_two")
        StepRun.objects.create(run=run, step=map_two, map_index=-1, status=StepRunStatus.SCHEDULED, input={})

    assert engine.advance(run.pk) == {"claimed": 0}

    run.refresh_from_db()
    with system_context(reason="test malformed Map journal"):
        maps = list(StepRun.objects.filter(run=run, step__step_class="map").order_by("pk"))
        has_children = StepRun.objects.filter(run=run, map_index__gte=0).exists()
    assert run.status == RunStatus.FAILED
    assert run.steps_taken == 0
    assert all(row.status == StepRunStatus.SCHEDULED for row in maps)
    assert not has_children


def test_map_exact_capacity_includes_parent_and_preexisting_scheduled_work(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """The boundary admits the Map, its child, and another scheduled row exactly once."""

    del workflow_engine_tables, no_workflow_queue
    workflow = _map_workflow(max_steps=3, items=["one"])
    run = start_run(workflow)
    with system_context(reason="test admitted workflow work"):
        body = workflow.steps.get(key="body_one")
        StepRun.objects.create(run=run, step=body, map_index=-1, status=StepRunStatus.SCHEDULED, input={})

    assert engine.advance(run.pk) == {"claimed": 2}

    run.refresh_from_db()
    with system_context(reason="test exact Map capacity"):
        rows = list(StepRun.objects.filter(run=run).order_by("step__key", "map_index"))
    assert run.status == RunStatus.RUNNING
    assert run.steps_taken == 3
    assert sum(row.map_index >= 0 for row in rows) == 1
    assert sum(row.status == StepRunStatus.STARTED for row in rows) == 2


def test_two_maps_cannot_spend_the_same_remaining_capacity(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """A later Map observes children admitted by an earlier Map in the same advance."""

    del workflow_engine_tables, no_workflow_queue
    workflow = _map_workflow(max_steps=3, items=["one"], two_maps=True)
    run = start_run(workflow)
    with system_context(reason="test simultaneous Maps"):
        map_two = workflow.steps.get(key="map_two")
        StepRun.objects.create(run=run, step=map_two, map_index=-1, status=StepRunStatus.SCHEDULED, input={})

    assert engine.advance(run.pk) == {"claimed": 0}

    run.refresh_from_db()
    with system_context(reason="test Map capacity ownership"):
        first_child_count = StepRun.objects.filter(run=run, step__key="body_one", map_index__gte=0).count()
        has_second_children = StepRun.objects.filter(run=run, step__key="body_two", map_index__gte=0).exists()
        second_map = StepRun.objects.get(run=run, step__key="map_two")
    assert run.status == RunStatus.FAILED
    assert run.steps_taken == 1
    assert first_child_count == 1
    assert not has_second_children
    assert second_map.status == StepRunStatus.SCHEDULED
    assert second_map.resume_state == {}


def test_waiting_map_recovery_counts_existing_children_once(
    workflow_engine_tables: None,
    no_workflow_queue: None,
) -> None:
    """Recovery may fill a missing child when the parent and existing journal already spent capacity."""

    del workflow_engine_tables, no_workflow_queue
    workflow = _map_workflow(max_steps=3)
    run = start_run(workflow)
    with system_context(reason="test partial Map journal"):
        map_row = StepRun.objects.select_related("step").get(run=run, step__key="map_one")
        body = workflow.steps.get(key="body_one")
        map_row.status = StepRunStatus.WAITING
        map_row.resume_state = {
            "map": {"target_step_key": body.key, "target_step_id": body.pk, "items": ["one", "two"]}
        }
        map_row.save(update_fields=["status", "resume_state", "updated_at"])
        StepRun.objects.create(
            run=run,
            step=body,
            map_index=0,
            status=StepRunStatus.SCHEDULED,
            input={"item": "one"},
        ).previous.add(map_row)
        WorkflowRun.objects.filter(pk=run.pk).update(status=RunStatus.RUNNING, steps_taken=1)

    assert engine.advance(run.pk) == {"claimed": 2}

    run.refresh_from_db()
    with system_context(reason="test recovered Map journal"):
        children = list(StepRun.objects.filter(run=run, step__key="body_one").order_by("map_index"))
    assert run.steps_taken == 3
    assert [child.map_index for child in children] == [0, 1]
    assert all(child.status == StepRunStatus.STARTED for child in children)
