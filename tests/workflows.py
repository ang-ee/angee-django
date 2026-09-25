"""Synchronous workflow engine and graph helpers for framework tests."""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from rebac import RelationshipTuple, system_context, to_subject_ref, write_relationships
from rebac.resources import to_object_ref

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.steps import StepImpl, StepResult
from angee.workflows.testing.drivers import start_run as start_workflow_run
from angee.workflows.testing.models import Edge, Step, Workflow


class FixtureStep(StepImpl):
    """Concrete configurable operation used only by workflow runtime tests."""

    key = "fixture"
    label = "Fixture"
    category = "Tests"
    selectable = False
    deterministic = False

    def run(self, step_run: Any, *, now: Any) -> StepResult:
        """Return the explicitly configured test output and outcome."""

        del self, now
        config = dict(step_run.step.config)
        if config.get("mode") == "error":
            raise RuntimeError(str(config.get("error", "fixture failed")))
        return StepResult.done(
            output={
                "key": step_run.step.key,
                "input": step_run.input,
                **dict(config.get("output", {})),
            },
            outcome=str(config.get("outcome", "done")),
        )


@pytest.fixture()
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow tests synchronous by replacing queue enqueue hooks."""

    from angee.workflows import dispatch

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id, **kwargs: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when, **kwargs: None)
    monkeypatch.setattr(engine, "enqueue_dispatch_publisher", lambda **kwargs: None)
    monkeypatch.setattr(dispatch, "enqueue_task", lambda *args, **kwargs: None)


def workflow_actor() -> Any:
    """Return the ordinary author used by source workflow graph fixtures."""

    with system_context(reason="test workflow fixture author"):
        return get_user_model().objects.get_or_create(username="workflow-fixture-author")[0]


def admit_workflow_actor(workflow: Any, actor: Any = None) -> Any:
    """Give one fixture actor the workflow edit permission needed to start it."""

    actor = actor if actor is not None else workflow_actor()
    with system_context(reason="test workflow action admission"):
        head = workflow.published_from if workflow.published_from_id is not None else workflow
        write_relationships([RelationshipTuple(to_object_ref(head), "editor", to_subject_ref(actor))])
    return actor


def start_run(workflow: Any, *, subject: Any = None, actor: Any = None) -> Any:
    """Execute source fixtures as an explicitly authorized test actor."""

    return start_workflow_run(workflow, subject=subject, actor=admit_workflow_actor(workflow, actor))


def workflow_with_steps(
    *,
    actor: Any = None,
    name: str = "Engine",
    key: str = "",
    purpose: workflow_models.WorkflowPurpose = workflow_models.WorkflowPurpose.AUTOMATION,
    subject_declaration: str = "",
    max_steps: int = 1000,
    budget: dict[str, Any] | None = None,
    steps: tuple[dict[str, Any], ...],
    edges: tuple[tuple[str, str, str], ...],
) -> Workflow:
    """Create and publish a workflow definition graph."""

    with system_context(reason="test workflows definition"):
        draft = Workflow.objects.create(
            created_by=actor if actor is not None else workflow_actor(),
            key=key,
            name=name,
            purpose=purpose,
            subject_declaration=subject_declaration,
            max_steps=max_steps,
            budget=budget or {},
        )
        by_key = {}
        for index, spec in enumerate(steps):
            by_key[spec["key"]] = Step.objects.create(
                workflow=draft,
                key=spec["key"],
                name=spec.get("name", spec["key"].replace("_", " ").title()),
                step_class=spec.get("step_class", "fixture"),
                config=spec.get("config", {}),
                input_binding=spec.get("input_binding"),
                join_rule=spec.get("join_rule", workflow_models.JoinRule.ALL_SUCCESS),
                is_entry=index == 0 if "is_entry" not in spec else spec["is_entry"],
            )
        for source, target, condition in edges:
            Edge.objects.create(workflow=draft, source=by_key[source], target=by_key[target], condition=condition)
        return draft.publish()


def step_for(workflow: Workflow, key: str) -> Step:
    """Return one workflow step under elevated test read context."""

    with system_context(reason="test workflows step read"):
        return Step.objects.get(workflow=workflow, key=key)
