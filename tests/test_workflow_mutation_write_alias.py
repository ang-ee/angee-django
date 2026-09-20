"""Workflow mutation preflight keeps secondary targets on the selected writer."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from django.db import connection, connections, models, router
from rebac import system_context

from angee.graphql.ids import to_public_id
from angee.workflows import engine
from tests.conftest import create_platform_admin, execute_schema, result_data
from tests.test_transitions import TransitionRouter
from tests.test_workflow_test_snapshots import _draft
from tests.test_workflows import _console_schema
from tests.workflows import Step, WorkflowRun


@pytest.fixture
def mutation_writer(workflow_engine_tables: None) -> Iterator[str]:
    """Reuse the transition tests' dynamic connection after workflow tables exist."""

    del workflow_engine_tables
    alias = "workflow_mutation_writer"
    connections[alias] = connection.copy(alias=alias)
    try:
        yield alias
    finally:
        connections[alias].close()
        del connections[alias]


class _OverrideRouter(TransitionRouter):
    """Only the primary run may select a writer; related steps must inherit it."""

    def db_for_read(self, model: type[models.Model], **hints: Any) -> str:
        if model._meta.app_label == "workflows":
            return super().db_for_read(model, **hints)
        return "default"

    def db_for_write(self, model: type[models.Model], **hints: Any) -> str:
        if model._meta.app_label != "workflows":
            return "default"
        assert model is WorkflowRun, "Related workflow targets must inherit the primary writer."
        assert not self.writes, "The mutation must select its writer only once."
        return super().db_for_write(model, **hints)


@pytest.mark.parametrize("missing_step", [False, True])
def test_override_mutation_resolves_related_steps_on_the_run_writer(
    mutation_writer: str, no_workflow_queue: None, monkeypatch: pytest.MonkeyPatch, missing_step: bool
) -> None:
    """Real GraphQL preflight resolves or rejects a secondary before domain dispatch."""

    del no_workflow_queue
    schema = _console_schema()
    actor = create_platform_admin("override-preflight-admin")
    workflow, step = _draft(owner=actor)
    with system_context(reason="test override preflight fixture"):
        run = WorkflowRun.objects.create(workflow=workflow, created_by=actor)
    selected_ids = [step.sqid]
    if missing_step:
        selected_ids.append(to_public_id(Step, step.pk + 1))
    dispatched: list[tuple[WorkflowRun, tuple[Step, ...], Any, str | None]] = []

    def record_override(
        target: WorkflowRun, next_steps: Iterable[Step], *, actor: Any, using: str | None = None
    ) -> SimpleNamespace:
        dispatched.append((target, tuple(next_steps), actor, using))
        return SimpleNamespace(sqid="override-preflight")

    routing = _OverrideRouter(mutation_writer)
    with monkeypatch.context() as patch:
        patch.setattr(engine, "override_run", record_override)
        patch.setattr(router, "routers", [routing])
        result = execute_schema(
            schema,
            """
            mutation Override($run: ID!, $steps: [ID!]!) {
              override_run(run: $run, next_steps: $steps) { ok message }
            }
            """,
            {"run": run.sqid, "steps": selected_ids},
            user=actor,
        )

    assert routing.writes == [None]
    if missing_step:
        assert result.errors is not None
        assert "was not found" in result.errors[0].message
        assert dispatched == []
    else:
        assert result_data(result)["override_run"]["ok"] is True
        assert len(dispatched) == 1
        target, selected, dispatched_actor, alias = dispatched[0]
        assert target.pk == run.pk
        assert [item.pk for item in selected] == [step.pk]
        assert target._state.db == selected[0]._state.db == alias == mutation_writer
        assert dispatched_actor is actor
