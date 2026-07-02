"""Tests for the workflows-agents composition addon.

The addon contributes one non-deterministic ``agent`` workflow activity through
the workflow step registry. Agent gate dispatch is intentionally absent here: it
depends on the deferred zed subject-union extension decision.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from rebac import system_context

from angee.agents.backends import InferenceRequest, InferenceResponse
from angee.workflows import engine
from angee.workflows import models as workflow_models
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    StubInferenceBackend,
    _clear_model_tables,
    _create_missing_tables,
)
from tests.test_agents import InferenceModel, _provider
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent
from tests.test_workflows import Edge, Step, Trigger, Workflow
from tests.test_workflows_engine import StepRun, WorkflowRun, advance_once, execute_started, step_run_for

User = get_user_model()


@pytest.fixture()
def workflows_agents_tables(transactional_db: Any) -> Iterator[None]:
    """Create workflow runtime plus agent catalogue test tables."""

    del transactional_db
    models = (
        IAM_CONNECTION_TEST_MODELS
        + INTEGRATE_TEST_MODELS
        + AGENTS_GRAPHQL_MODELS
        + (Workflow, Step, Edge, Trigger, WorkflowRun, StepRun)
    )
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
def no_workflow_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow-agent tests synchronous by replacing queue enqueue hooks."""

    monkeypatch.setattr(engine, "enqueue_advance", lambda run_id: None)
    monkeypatch.setattr(engine, "enqueue_advance_at", lambda run_id, when: None)
    monkeypatch.setattr(engine, "enqueue_execute", lambda step_run_id: None)


@pytest.fixture()
def stub_chats(monkeypatch: pytest.MonkeyPatch) -> list[InferenceRequest]:
    """Capture chat requests sent through the configured stub inference backend."""

    calls: list[InferenceRequest] = []

    def chat(self: StubInferenceBackend, request: InferenceRequest) -> InferenceResponse:
        del self
        calls.append(request)
        return InferenceResponse(
            text="stub response " + ("x" * 6000),
            content=[{"type": "text", "text": "stub response"}],
            usage={"input_tokens": 2, "output_tokens": 3},
        )

    monkeypatch.setattr(StubInferenceBackend, "chat", chat)
    return calls


def test_agent_step_renders_template_and_journals_bounded_io(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    stub_chats: list[InferenceRequest],
) -> None:
    """An agent-configured step renders subject/run/step context and bounds its journal."""

    del workflows_agents_tables, no_workflow_queue
    from angee.workflows_agents.steps import AGENT_STEP_JOURNAL_MAX_BYTES, AGENT_STEP_TRUNCATION_MARKER

    subject = User.objects.create_user(username="workflow-subject")
    model = _inference_model("stub-render")
    with system_context(reason="test workflows agent setup"):
        agent = Agent.objects.create(
            name="Workflow reviewer",
            owner=subject,
            instructions="Answer with a short summary.",
            model=model,
        )
    workflow = _published_workflow(
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "agent": agent.sqid,
                    "prompt_template": "Review {{ subject.username }} in {{ step.key }} for run {{ run.pk }}.",
                    "max_tokens": 32,
                },
            },
        ),
        edges=(),
    )

    run = _start_run(workflow, subject=subject)
    advance_once(run)
    execute_started(run)
    engine.advance(run.pk)

    row = step_run_for(run, "agent")
    encoded_output = json.dumps(row.output, sort_keys=True)
    assert row.outcome == "completed"
    assert stub_chats[0].model == model.name
    assert stub_chats[0].system == "Answer with a short summary."
    assert stub_chats[0].messages == [
        {"role": "user", "content": f"Review workflow-subject in agent for run {run.pk}."}
    ]
    assert len(encoded_output.encode("utf-8")) <= AGENT_STEP_JOURNAL_MAX_BYTES
    assert AGENT_STEP_TRUNCATION_MARKER in encoded_output
    assert "workflow-subject" in encoded_output


def test_agent_step_debits_token_usage_into_run_budget_spent(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    stub_chats: list[InferenceRequest],
) -> None:
    """Token usage returned by the backend lands on the run budget ledger."""

    del workflows_agents_tables, no_workflow_queue, stub_chats
    model = _inference_model("stub-budget")
    workflow = _published_workflow(
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "provider": model.provider.sqid,
                    "model": model.name,
                    "prompt_template": "Count tokens.",
                },
            },
        ),
        edges=(),
    )

    run = _start_run(workflow)
    advance_once(run)
    execute_started(run)
    run.refresh_from_db()

    assert run.budget_spent == {"input_tokens": 2, "output_tokens": 3, "tokens": 5}


def test_budget_ceiling_fails_run_via_engine(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    stub_chats: list[InferenceRequest],
) -> None:
    """The engine fails a run whose journaled token spend exceeds its budget."""

    del workflows_agents_tables, no_workflow_queue, stub_chats
    run_status, step_status = workflow_models.RunStatus, workflow_models.StepRunStatus
    model = _inference_model("stub-ceiling")
    workflow = _published_workflow(
        budget={"tokens": 4},
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "provider": model.provider.sqid,
                    "model": model.name,
                    "prompt_template": "Spend tokens.",
                },
            },
            {"key": "finish", "config": {"outcome": "done"}},
        ),
        edges=(("agent", "finish", "completed"),),
    )

    run = _start_run(workflow)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    run.refresh_from_db()

    assert run.status == run_status.FAILED
    assert "budget" in run.error
    assert "tokens" in run.error
    assert step_run_for(run, "finish").status == step_status.SCHEDULED


def test_replay_does_not_reinvoke_completed_agent_step(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    stub_chats: list[InferenceRequest],
) -> None:
    """Replaying a completed agent activity reuses the journaled output."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("stub-replay")
    workflow = _published_workflow(
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "provider": model.provider.sqid,
                    "model": model.name,
                    "prompt_template": "Run once.",
                },
            },
        ),
        edges=(),
    )
    run = _start_run(workflow)
    row = advance_once(run)[0]

    execute_started(run)
    engine.execute(row.pk)
    engine.advance(run.pk)
    engine.advance(run.pk)

    assert len(stub_chats) == 1


def test_backend_error_routes_failed_outcome(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backend errors are journaled as a failed outcome that normal edge routing can use."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("stub-error")

    def chat(self: StubInferenceBackend, request: InferenceRequest) -> InferenceResponse:
        del self, request
        raise RuntimeError("backend unavailable")

    monkeypatch.setattr(StubInferenceBackend, "chat", chat)
    workflow = _published_workflow(
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "provider": model.provider.sqid,
                    "model": model.name,
                    "prompt_template": "Fail gracefully.",
                },
            },
            {"key": "on_failed", "config": {"outcome": "done"}},
        ),
        edges=(("agent", "on_failed", "failed"),),
    )
    run = _start_run(workflow)

    advance_once(run)
    execute_started(run)
    advance_once(run)

    agent_row = step_run_for(run, "agent")
    failed_row = step_run_for(run, "on_failed")
    assert agent_row.status == workflow_models.StepRunStatus.SUCCEEDED
    assert agent_row.outcome == "failed"
    assert agent_row.output["error"]["message"] == "backend unavailable"
    assert failed_row.status == workflow_models.StepRunStatus.STARTED


def _inference_model(slug: str) -> InferenceModel:
    """Create one stub-backed inference model for workflow-agent tests."""

    provider = _provider(slug, backend_class="stub_inference", name="Stub provider")
    with system_context(reason="test workflows agent model setup"):
        return InferenceModel.objects.create(provider=provider, name=f"{slug}-model")


def _published_workflow(
    *,
    steps: tuple[dict[str, Any], ...],
    edges: tuple[tuple[str, str, str], ...],
    budget: dict[str, Any] | None = None,
) -> Workflow:
    """Create and publish a workflow definition graph for workflow-agent tests."""

    with system_context(reason="test workflows agent definition"):
        draft = Workflow.objects.create(name="Workflow agent", budget=budget or {})
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
            Edge.objects.create(
                workflow=draft,
                source=by_key[source],
                target=by_key[target],
                condition=condition,
            )
        return draft.publish()


def _start_run(workflow: Workflow, *, subject: Any = None) -> WorkflowRun:
    """Start one workflow run with an optional subject object."""

    return engine.start(workflow, subject=subject, actor=None)
