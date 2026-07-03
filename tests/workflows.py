"""Shared workflow test models, tables, and synchronous engine helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.models import (
    Edge as AbstractEdge,
)
from angee.workflows.models import (
    Step as AbstractStep,
)
from angee.workflows.models import (
    Trigger as AbstractTrigger,
)
from angee.workflows.models import (
    Workflow as AbstractWorkflow,
)
from tests.conftest import _clear_model_tables, _create_missing_tables


class Workflow(AbstractWorkflow):
    """Concrete workflow model for source-addon tests."""

    class Meta(AbstractWorkflow.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow"
        rebac_resource_type = "workflows/workflow"
        rebac_id_attr = "sqid"


class Step(AbstractStep):
    """Concrete workflow step model for source-addon tests."""

    class Meta(AbstractStep.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step"
        rebac_resource_type = "workflows/step"
        rebac_id_attr = "sqid"


class Edge(AbstractEdge):
    """Concrete workflow edge model for source-addon tests."""

    class Meta(AbstractEdge.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_edge"
        rebac_resource_type = "workflows/edge"
        rebac_id_attr = "sqid"


class Trigger(AbstractTrigger):
    """Concrete workflow trigger model for source-addon tests."""

    class Meta(AbstractTrigger.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_trigger"
        rebac_resource_type = "workflows/trigger"
        rebac_id_attr = "sqid"


class WorkflowRun(workflow_models.WorkflowRun):
    """Concrete workflow run model for source-addon engine tests."""

    class Meta(workflow_models.WorkflowRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_workflow_run"
        rebac_resource_type = "workflows/run"
        rebac_id_attr = "sqid"


class StepRun(workflow_models.StepRun):
    """Concrete workflow step-run journal model for source-addon engine tests."""

    class Meta(workflow_models.StepRun.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_step_run"
        rebac_resource_type = "workflows/step_run"
        rebac_id_attr = "sqid"


class Decision(workflow_models.Decision):
    """Concrete decision model for source-addon runtime tests."""

    class Meta(workflow_models.Decision.Meta):
        abstract = False
        app_label = "workflows"
        db_table = "test_workflows_decision"
        rebac_resource_type = "workflows/decision"
        rebac_id_attr = "sqid"


WORKFLOW_DEFINITION_MODELS = (Workflow, Step, Edge, Trigger)
WORKFLOW_RUNTIME_MODELS = (*WORKFLOW_DEFINITION_MODELS, WorkflowRun, StepRun, Decision)


@contextmanager
def workflow_table_setup(models: tuple[type[Any], ...]) -> Iterator[None]:
    """Create missing workflow tables, sync permissions, clear rows, then drop created tables."""

    created = _create_missing_tables(models)
    call_command("rebac", "sync", verbosity=0)
    _clear_model_tables(models)
    try:
        yield
    finally:
        _clear_model_tables(models)
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


@pytest.fixture()
def workflow_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete workflow definition tables."""

    del transactional_db
    with workflow_table_setup(WORKFLOW_DEFINITION_MODELS):
        yield


@pytest.fixture()
def workflow_engine_tables(transactional_db: Any) -> Iterator[None]:
    """Create concrete workflow runtime tables."""

    del transactional_db
    with workflow_table_setup(WORKFLOW_RUNTIME_MODELS):
        yield


@pytest.fixture()
def workflow_gate_tables(workflow_engine_tables: None) -> None:
    """Alias runtime tables for gate tests."""

    del workflow_engine_tables


@pytest.fixture()
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow tests synchronous by replacing queue enqueue hooks."""

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when: None)
    monkeypatch.setattr(engine, "enqueue_execute", lambda step_run_id: None)
    monkeypatch.setattr(engine, "enqueue_decision_escalation_at", lambda decision_id, attempt, when: None)
    monkeypatch.setattr(engine, "enqueue_decision_expiry_at", lambda decision_id, attempt, when: None)


def workflow_with_steps(
    *,
    name: str = "Engine",
    max_steps: int = 1000,
    budget: dict[str, Any] | None = None,
    steps: tuple[dict[str, Any], ...],
    edges: tuple[tuple[str, str, str], ...],
) -> Workflow:
    """Create and publish a workflow definition graph."""

    with system_context(reason="test workflows definition"):
        draft = Workflow.objects.create(name=name, max_steps=max_steps, budget=budget or {})
        by_key = {}
        for index, spec in enumerate(steps):
            by_key[spec["key"]] = Step.objects.create(
                workflow=draft,
                key=spec["key"],
                name=spec.get("name", spec["key"].replace("_", " ").title()),
                step_class=spec.get("step_class", "handler"),
                config=spec.get("config", {}),
                join_rule=spec.get("join_rule", workflow_models.JoinRule.ALL_SUCCESS),
                is_entry=index == 0 if "is_entry" not in spec else spec["is_entry"],
            )
        for source, target, condition in edges:
            Edge.objects.create(workflow=draft, source=by_key[source], target=by_key[target], condition=condition)
        return draft.publish()


def start_run(workflow: Workflow, *, subject: Any = None) -> WorkflowRun:
    """Start a run without relying on a live queue."""

    return engine.start(workflow, subject=subject, actor=None)


def advance_once(run: Any, *, now: Any | None = None) -> list[Any]:
    """Advance one run and return started rows."""

    if now is None:
        engine.advance(run.pk)
    else:
        engine.advance(run.pk, now=now)
    with system_context(reason="test workflows read started"):
        return list(StepRun.objects.filter(run=run, status=workflow_models.StepRunStatus.STARTED).order_by("pk"))


def execute_started(run: Any, *, now: Any | None = None, limit: int | None = None) -> None:
    """Execute currently started step-runs synchronously."""

    with system_context(reason="test workflows read started"):
        rows = list(StepRun.objects.filter(run=run, status=workflow_models.StepRunStatus.STARTED).order_by("pk"))
    if limit is not None:
        rows = rows[:limit]
    for row in rows:
        if now is None:
            engine.execute(row.pk)
        else:
            engine.execute(row.pk, now=now)


def run_to_terminal(run: Any, *, max_cycles: int = 20) -> Any:
    """Drive a run synchronously until it reaches a terminal state."""

    for _ in range(max_cycles):
        run.refresh_from_db()
        if run.status in workflow_models.RunStatus.TERMINAL:
            return run
        advance_once(run)
        execute_started(run)
        run.refresh_from_db()
        with system_context(reason="test workflows active check"):
            active = StepRun.objects.filter(
                run=run,
                status__in=[workflow_models.StepRunStatus.SCHEDULED, workflow_models.StepRunStatus.STARTED],
            ).exists()
        if not active:
            advance_once(run)
    run.refresh_from_db()
    return run


def step_run_for(run: Any, key: str) -> Any:
    """Return one step-run row under elevated test read context."""

    with system_context(reason="test workflows step_run read"):
        return StepRun.objects.get(run=run, step__key=key)


def step_for(workflow: Workflow, key: str) -> Step:
    """Return one workflow step under elevated test read context."""

    with system_context(reason="test workflows step read"):
        return Step.objects.get(workflow=workflow, key=key)
