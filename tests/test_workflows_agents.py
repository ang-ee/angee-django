"""Tests for the workflows-agents composition addon.

The addon contributes one non-deterministic ``agent`` workflow activity through
the workflow step registry. Agent gate dispatch is intentionally absent here: it
depends on the deferred zed subject-union extension decision.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from rebac import actor_context, current_actor, system_context, to_subject_ref

from angee.agents.backends import InferenceRequest, InferenceResponse
from angee.agents.models import AgentLifecycle, RuntimeStatus, SessionStatus, TurnStatus
from angee.agents.runners import TurnOutcome
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.steps import TransientStepError
from angee.workflows_agents import sessions
from tests.conftest import (
    IAM_CONNECTION_TEST_MODELS,
    INTEGRATE_TEST_MODELS,
    StubInferenceBackend,
    _create_missing_tables,
)
from tests.test_agents import InferenceModel, _provider
from tests.test_agents_graphql import AGENTS_GRAPHQL_MODELS, Agent, AgentSession, AgentTurn
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    advance_once,
    execute_started,
    start_run,
    step_run_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()
pytest_plugins = ("tests.workflows",)


@pytest.fixture()
def workflows_agents_tables(transactional_db: Any) -> Iterator[None]:
    """Create workflow runtime plus agent catalogue test tables."""

    del transactional_db
    models = IAM_CONNECTION_TEST_MODELS + INTEGRATE_TEST_MODELS + AGENTS_GRAPHQL_MODELS + WORKFLOW_RUNTIME_MODELS
    created = _create_missing_tables(models)
    try:
        with workflow_table_setup(models):
            yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


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
    workflow = workflow_with_steps(
        name="Workflow agent",
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

    run = start_run(workflow, subject=subject)
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
    workflow = workflow_with_steps(
        name="Workflow agent",
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

    run = start_run(workflow)
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
    workflow = workflow_with_steps(
        name="Workflow agent",
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

    run = start_run(workflow)
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
    workflow = workflow_with_steps(
        name="Workflow agent",
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
    run = start_run(workflow)
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
    workflow = workflow_with_steps(
        name="Workflow agent",
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
    run = start_run(workflow)

    advance_once(run)
    execute_started(run)
    advance_once(run)

    agent_row = step_run_for(run, "agent")
    failed_row = step_run_for(run, "on_failed")
    assert agent_row.status == workflow_models.StepRunStatus.SUCCEEDED
    assert agent_row.outcome == "failed"
    assert agent_row.output["error"]["message"] == "backend unavailable"
    assert failed_row.status == workflow_models.StepRunStatus.STARTED


def test_agent_step_reraises_transient_backend_errors(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retryable provider errors stay transient for the workflow task strategy."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("stub-transient")

    def chat(self: StubInferenceBackend, request: InferenceRequest) -> InferenceResponse:
        del self, request
        raise TransientStepError("rate limited")

    monkeypatch.setattr(StubInferenceBackend, "chat", chat)
    workflow = workflow_with_steps(
        name="Workflow agent",
        steps=(
            {
                "key": "agent",
                "step_class": "agent",
                "config": {
                    "provider": model.provider.sqid,
                    "model": model.name,
                    "prompt_template": "Retry later.",
                    "retry": {"max_attempts": 2},
                },
            },
        ),
        edges=(),
    )
    run = start_run(workflow)
    step_run = advance_once(run)[0]

    with pytest.raises(TransientStepError, match="rate limited"):
        engine.execute(step_run.pk)

    step_run.refresh_from_db()
    assert step_run.status == workflow_models.StepRunStatus.STARTED


def test_session_and_turn_reads_and_turn_subscription_are_owner_gated(
    workflows_agents_tables: None,
) -> None:
    """A non-owner cannot query a session/turn or receive its change notification."""

    del workflows_agents_tables
    owner = User.objects.create_user(username="session-owner")
    stranger = User.objects.create_user(username="session-stranger")
    with system_context(reason="test workflows agents rebac seed"):
        agent = Agent.objects.create(name="Private agent", owner=owner, runtime_class="pydantic")
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="private prompt")

    assert list(AgentSession.objects.as_user(owner)) == [session]
    assert list(AgentTurn.objects.as_user(owner)) == [turn]
    assert list(AgentSession.objects.as_user(stranger)) == []
    assert list(AgentTurn.objects.as_user(stranger)) == []

    change = ChangePayload.from_instance(turn, action="update", update_fields={"status"})
    assert ChangeReadGate(AgentTurn, to_subject_ref(owner)).filter(change) is not None
    assert ChangeReadGate(AgentTurn, to_subject_ref(stranger)).filter(change) is None


def test_delivery_generation_closes_the_post_between_park_and_waiting_race(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post delivered after the park decision is immediately reclaimed, not stranded."""

    del workflows_agents_tables, no_workflow_queue
    from angee.agents_runtime_pydantic.runtime import PydanticAIRuntime
    from angee.workflows_agents.steps import AgentSessionStepImpl

    owner, agent = _ready_session_agent("lost-wakeup")
    _session_workflow()
    session = sessions.start_session(agent, owner=owner, context={})
    with system_context(reason="test lost wakeup run"):
        run = sessions.run_for(session)
    step_run = advance_once(run)[0]
    original_run = AgentSessionStepImpl.run
    late_turns: list[Any] = []

    class FakeRunner:
        def run_turn(self, session: Any, turn: Any, **kwargs: Any) -> TurnOutcome:
            kwargs["heartbeat"]()
            kwargs["emit"](
                {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "caught"}}
            )
            return TurnOutcome(kind="completed", text="caught", replay_state=[], usage={"requests": 1})

    monkeypatch.setattr(PydanticAIRuntime, "session_runner", lambda self: FakeRunner())

    def park_then_post(self: AgentSessionStepImpl, claimed: Any, *, now: Any) -> Any:
        result = original_run(self, claimed, now=now)
        if not late_turns:
            late_turns.append(sessions.post_message(session, "arrived during park"))
        return result

    monkeypatch.setattr(AgentSessionStepImpl, "run", park_then_post)
    engine.execute(step_run.pk)

    step_run.refresh_from_db()
    run.refresh_from_db()
    assert run.deliveries == 1
    assert step_run.claimed_deliveries == 0
    assert step_run.status == workflow_models.StepRunStatus.WAITING
    assert step_run.wait_until is not None and step_run.wait_until < timezone.now() + timedelta(seconds=1)

    monkeypatch.setattr(AgentSessionStepImpl, "run", original_run)
    assert engine.advance(run.pk) == {"claimed": 1}
    engine.execute(step_run.pk)

    late_turns[0].refresh_from_db()
    assert late_turns[0].status == TurnStatus.COMPLETED
    assert late_turns[0].text == "caught"


def test_quiet_turn_heartbeat_cadence_survives_reaper_then_expires_without_pulses(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runner pulses independently of emitted updates often enough for a 300s lease."""

    del workflows_agents_tables, no_workflow_queue
    from angee.agents_runtime_pydantic import runner as runner_module
    from angee.workflows_agents.steps import AgentSessionStepImpl

    settings.ANGEE_WORKFLOWS_HEARTBEAT_TIMEOUT = 300
    started_at = timezone.now()
    workflow = workflow_with_steps(
        name="Quiet heartbeat",
        steps=({"key": "quiet", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)
    step_run = advance_once(run, now=started_at)[0]
    clock = {"now": started_at, "sleeps": 0}

    class StopHeartbeat(Exception):
        pass

    async def advance_clock(seconds: float) -> None:
        assert seconds == 60
        clock["sleeps"] += 1
        if clock["sleeps"] > 5:
            raise StopHeartbeat
        clock["now"] += timedelta(seconds=seconds)

    def pulse() -> None:
        AgentSessionStepImpl().heartbeat(step_run, at=clock["now"])

    monkeypatch.setattr(runner_module.asyncio, "sleep", advance_clock)
    with pytest.raises(StopHeartbeat):
        async_to_sync(runner_module._heartbeat_loop)(pulse)

    step_run.refresh_from_db()
    assert step_run.heartbeat_at == started_at + timedelta(seconds=300)
    assert engine.reap(now=started_at + timedelta(seconds=301)) == {"reaped": 0}
    assert engine.reap(now=started_at + timedelta(seconds=601)) == {"reaped": 1}


def test_builtin_style_tool_turn_keeps_agent_actor_and_async_db_boundary(
    workflows_agents_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake builtin tool observes the agent actor and performs ORM work asynchronously."""

    del workflows_agents_tables
    from angee.agents_runtime_pydantic import runner as runner_module

    owner, agent = _ready_session_agent("builtin-tool")
    with system_context(reason="test builtin tool session"):
        session = AgentSession.objects.create(agent=agent, owner=owner)
        turn = AgentTurn.objects.create(session=session, index=1, prompt="Who owns me?")
    observed: list[tuple[Any, str]] = []

    async def builtin_read_owner() -> str:
        actor = current_actor()

        def read_owner() -> str:
            with system_context(reason="test builtin tool db boundary"):
                return User.objects.get(pk=owner.pk).username

        username = await database_sync_to_async(
            read_owner,
            thread_sensitive=True,
        )()
        observed.append((actor, username))
        return username

    monkeypatch.setattr(
        runner_module,
        "model_for_agent",
        lambda selected: TestModel(call_tools=["builtin_read_owner"], custom_output_text="done"),
    )
    monkeypatch.setattr(
        runner_module,
        "toolsets_for_agent",
        lambda selected: [FunctionToolset([builtin_read_owner])],
    )

    with actor_context(agent.principal_subject()):
        outcome = runner_module.PydanticAISessionRunner().run_turn(
            session,
            turn,
            deferred_results=[],
            emit=lambda update: None,
            heartbeat=lambda: None,
        )

    assert outcome.kind == "completed"
    assert observed == [(agent.principal_subject(), owner.username)]


def test_retrying_running_turn_discards_partial_updates(
    workflows_agents_tables: None,
) -> None:
    """Reclaiming a running turn starts a clean transcript for the retry attempt."""

    del workflows_agents_tables
    from angee.workflows_agents.steps import _claim_turn

    owner, agent = _ready_session_agent("retry-reset")
    with system_context(reason="test retry reset seed"):
        session = AgentSession.objects.create(agent=agent, owner=owner, status=SessionStatus.RUNNING)
        turn = AgentTurn.objects.create(
            session=session,
            index=1,
            prompt="retry me",
            status=TurnStatus.RUNNING,
            updates=[{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "partial"}}],
        )

    with system_context(reason="test retry reset claim"), transaction.atomic():
        claimed, resumed = _claim_turn(session)

    turn.refresh_from_db()
    assert claimed is not None and claimed.pk == turn.pk
    assert resumed is False
    assert turn.status == TurnStatus.RUNNING
    assert turn.updates == []


def test_transient_exhaustion_fails_turn_and_parks_session(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A spent retry budget becomes a failed TURN on a parked run, never a failed run.

    Regression for the first real-data smoke: a provider 429 raised
    ``TransientStepError`` past the step's retry budget, the engine journaled
    the step failed, and the whole session run died with the turn stuck
    RUNNING and no error text anywhere.
    """

    del workflows_agents_tables, no_workflow_queue
    from angee.agents_runtime_pydantic.runtime import PydanticAIRuntime

    owner, agent = _ready_session_agent("retry-exhaustion")
    _session_workflow()
    session = sessions.start_session(agent, owner=owner, context={})
    sessions.post_message(session, "hi")
    with system_context(reason="test exhaustion run"):
        run = sessions.run_for(session)
    step_run = advance_once(run)[0]

    class RateLimitedRunner:
        def run_turn(self, session: Any, turn: Any, **kwargs: Any) -> TurnOutcome:
            raise TransientStepError("status_code: 429, rate_limit_error")

    monkeypatch.setattr(PydanticAIRuntime, "session_runner", lambda self: RateLimitedRunner())
    # Default step policy is max_attempts=1: this execution is the final
    # attempt, so the impl converts instead of re-raising to the engine.
    engine.execute(step_run.pk)
    engine.advance(run.pk)

    step_run.refresh_from_db()
    run.refresh_from_db()
    session.refresh_from_db()
    assert run.status == workflow_models.RunStatus.WAITING
    assert step_run.status == workflow_models.StepRunStatus.WAITING
    assert session.status == SessionStatus.ERROR
    assert "429" in (session.last_error or "")
    with system_context(reason="test exhaustion verify"):
        turn = AgentTurn.objects.get(session=session, index=1)
        assert turn.status == TurnStatus.FAILED
        assert "429" in turn.error


def test_in_process_provision_and_teardown_leave_no_orphaned_waiting_run(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-process provisioning skips the operator and teardown closes/wakes every session."""

    del workflows_agents_tables, no_workflow_queue
    from angee.agents import provisioning

    owner = User.objects.create_user(username="provision-in-process-owner")
    with system_context(reason="test in-process provision seed"):
        agent = Agent.objects.create(name="In-process", owner=owner, runtime_class="pydantic")
    _session_workflow()

    def operator_must_not_be_called() -> Any:
        raise AssertionError("in-process provisioning must not call the operator")

    monkeypatch.setattr(provisioning.OperatorDaemon, "from_settings", operator_must_not_be_called)
    result = provisioning.provision_agent(agent.sqid)
    agent.refresh_from_db()
    assert result.ok is True
    assert agent.lifecycle == AgentLifecycle.READY
    assert agent.runtime_status == RuntimeStatus.RUNNING
    assert agent.workspace == "" and agent.service == ""

    session = sessions.start_session(agent, owner=owner, context={})
    with system_context(reason="test teardown session run"):
        run = sessions.run_for(session)
    advance_once(run)
    execute_started(run)
    engine.advance(run.pk)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.WAITING

    result = provisioning.deprovision_agent(agent.sqid)
    session.refresh_from_db()
    assert result.ok is True
    assert session.status == SessionStatus.CLOSED

    advance_once(run)
    execute_started(run)
    engine.advance(run.pk)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    with system_context(reason="test no waiting session rows"):
        assert not run.step_runs.filter(status=workflow_models.StepRunStatus.WAITING).exists()
        with pytest.raises(ProtectedError):
            agent.delete()


def _inference_model(slug: str) -> InferenceModel:
    """Create one stub-backed inference model for workflow-agent tests."""

    provider = _provider(slug, backend_class="stub_inference", name="Stub provider")
    with system_context(reason="test workflows agent model setup"):
        return InferenceModel.objects.create(provider=provider, name=f"{slug}-model")


def _ready_session_agent(slug: str) -> tuple[Any, Agent]:
    """Create an owner and already-running in-process agent for session tests."""

    owner = User.objects.create_user(username=f"{slug}-owner")
    with system_context(reason="test ready session agent"):
        agent = Agent.objects.create(
            name=slug,
            owner=owner,
            runtime_class="pydantic",
            lifecycle=AgentLifecycle.READY,
            runtime_status=RuntimeStatus.RUNNING,
        )
    return owner, agent


def _session_workflow() -> Any:
    """Publish the structural workflow selected by the session service."""

    return workflow_with_steps(
        name="Agent session fixture",
        subject_declaration="agents.agentsession",
        max_steps=100000,
        steps=({"key": "session", "step_class": "agent_session", "config": {}},),
        edges=(),
    )
