"""Tests for one-shot inference and resumable agent-session approvals."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset
from pydantic_ai.usage import RequestUsage
from rebac import actor_context, current_actor, system_context, to_subject_ref

from angee.agents.models import AgentLifecycle, RuntimeStatus, SessionStatus, TurnStatus
from angee.agents.runners import TurnOutcome
from angee.base.impl import resolve_impl_class
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows.attempts import AttemptResultKind, JsonPresence
from angee.workflows.steps import GateStep, StepImpl, TransientStepError
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
    StepAttempt,
    WorkflowDispatch,
    WorkflowRun,
    admit_workflow_actor,
    advance_once,
    execute_started,
    start_run,
    step_run_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()


@pytest.mark.django_db
def test_agent_approval_uses_dynamic_all_done_resumable_gate_slots() -> None:
    """Session tool calls use the built-in gate instead of authoring Decision specs."""

    from angee.workflows_agents.steps import _approval_gate_config

    owner = User.objects.create_user(username="approval-gate-owner")
    requests = [
        {"tool_call_id": "call-1", "name": "read_invoice", "args": {"id": "invoice-1"}},
        {"tool_call_id": "call-2", "name": "post_invoice", "args": {"id": "invoice-1"}},
    ]
    config = _approval_gate_config(SimpleNamespace(owner=owner), requests)
    result = GateStep.gate_result(
        SimpleNamespace(resume_state={}),
        config=config,
        retained_state={"turn": "turn-1"},
    )

    assert config["policy"] == "all_done"
    assert config["resume"] is True
    assert len(config["slots"]) == 2
    assert result.resume_state == {
        "gate": {"policy": "all_done"},
        "state": {"turn": "turn-1"},
        "_resume_after_decisions": True,
    }
    assert [decision.payload["tool_call_id"] for decision in result.decisions] == [
        "call-1",
        "call-2",
    ]
    assert all(decision.action == "approve_tool" for decision in result.decisions)
    assert result.decisions[0].decision_schema["oneOf"][1]["required"] == [
        "action",
        "reason",
    ]


@pytest.fixture()
def workflows_agents_tables(transactional_db: Any) -> Iterator[None]:
    """Create workflow runtime plus agent catalogue test tables."""

    del transactional_db
    from tests.test_workflows_resources import WorkflowResourceLedger

    models = (
        IAM_CONNECTION_TEST_MODELS
        + INTEGRATE_TEST_MODELS
        + AGENTS_GRAPHQL_MODELS
        + WORKFLOW_RUNTIME_MODELS
        + (WorkflowResourceLedger,)
    )
    created = _create_missing_tables(models)
    try:
        with workflow_table_setup(models):
            yield
    finally:
        if created:
            with connection.schema_editor() as schema_editor:
                for model in reversed(created):
                    schema_editor.delete_model(model)


def test_infer_is_the_only_registered_one_shot_step_key() -> None:
    """The cutover has one key and deliberately provides no ``agent`` alias."""

    from angee.workflows_agents.steps import InferStepImpl

    assert resolve_impl_class("ANGEE_WORKFLOW_STEP_CLASSES", "infer", StepImpl) is InferStepImpl
    with pytest.raises(ImproperlyConfigured, match="No impl for key 'agent'"):
        resolve_impl_class("ANGEE_WORKFLOW_STEP_CLASSES", "agent", StepImpl)


def test_infer_step_passes_native_request_envelope_and_projects_response(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Messages, images, schema, settings and timeout reach ``InferenceModel.infer`` unchanged."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("infer-envelope")
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = None
    schema = {
        "type": "object",
        "properties": {"classification": {"type": "string"}},
        "required": ["classification"],
        "additionalProperties": False,
    }
    captured: dict[str, Any] = {}
    debits: list[dict[str, int]] = []
    original_debit = WorkflowRun.debit_budget

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        captured.update(messages=messages, info=info)
        return ModelResponse(
            parts=[ToolCallPart("inference_output", {"classification": "invoice"}, "call-1")],
            usage=RequestUsage(input_tokens=7, output_tokens=3),
        )

    bindings = _stub_model_backend(monkeypatch, respond)

    def debit_budget(run: WorkflowRun, delta: dict[str, int]) -> None:
        debits.append(dict(delta))
        original_debit(run, delta)

    monkeypatch.setattr(WorkflowRun, "debit_budget", debit_budget)
    workflow = _infer_workflow()
    run = _start_infer_run(
        workflow,
        {
            "model": str(model.sqid),
            "role": "classification",
            "request": {
                "messages": [
                    {
                        "kind": "request",
                        "parts": [
                            {"part_kind": "system-prompt", "content": "Return the declared schema."},
                            {"part_kind": "user-prompt", "content": "Classify this document."},
                        ],
                    }
                ],
                "images": [{"kind": "binary", "data": "aW1hZ2U=", "media_type": "image/jpeg"}],
                "output_schema": schema,
                "settings": {"temperature": 0},
            },
            "timeout": 12.5,
        },
    )

    advance_once(run)
    execute_started(run)

    row = step_run_for(run, "infer")
    run.refresh_from_db()
    assert isinstance(captured["messages"][0], ModelRequest)
    assert isinstance(captured["messages"][0].parts[0], SystemPromptPart)
    assert isinstance(captured["messages"][0].parts[1], UserPromptPart)
    image = captured["messages"][1].parts[0].content[0]
    assert isinstance(image, BinaryContent)
    assert image.data == b"image"
    assert captured["info"].model_request_parameters.output_object.json_schema == schema
    assert captured["info"].model_settings == {"temperature": 0, "timeout": 12.5}
    assert bindings == [(model.provider_model_name, None)]
    assert row.outcome == "completed"
    assert row.output["response"]["kind"] == "response"
    assert row.output["response"]["parts"][0]["part_kind"] == "tool-call"
    assert row.output["response"]["parts"][0]["args"] == {"classification": "invoice"}
    assert row.output["usage"] == {"input_tokens": 7, "output_tokens": 3, "tokens": 10, "requests": 1}
    assert debits == [{"input_tokens": 7, "output_tokens": 3, "tokens": 10, "requests": 1}]
    assert run.budget_spent == {"input_tokens": 7, "output_tokens": 3, "tokens": 10, "requests": 1}


def test_infer_step_policy_absent_leaves_catalogue_unrestricted(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The policy owner's documented absent-setting behavior applies to the step."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("unrestricted-infer")
    settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = None
    bindings = _stub_model_backend(
        monkeypatch,
        lambda messages, info: ModelResponse(parts=[TextPart("allowed")]),
    )
    run = _start_infer_run(_infer_workflow(), _infer_input(model))
    advance_once(run)
    execute_started(run)

    assert bindings == [(model.provider_model_name, None)]
    assert step_run_for(run, "infer").outcome == "completed"


def test_infer_step_unresolved_model_id_is_an_invocation_error(
    workflows_agents_tables: None,
) -> None:
    """Catalogue resolution happens before provider outcome conversion or debit."""

    del workflows_agents_tables
    from angee.workflows_agents.steps import InferStepImpl

    model = _inference_model("missing-infer")
    public_id = str(model.sqid)
    with system_context(reason="test remove infer model"):
        model.delete()
    debits: list[dict[str, int]] = []
    step_run = SimpleNamespace(
        input={**_infer_input(), "model": public_id},
        run=SimpleNamespace(debit_budget=lambda delta: debits.append(dict(delta))),
        _state=SimpleNamespace(adding=False, db="default"),
    )

    with pytest.raises(InferenceModel.DoesNotExist):
        InferStepImpl().run(step_run, now=timezone.now())
    assert debits == []


@pytest.mark.parametrize("rejection", ["role", "endpoint"])
def test_infer_step_rejects_unapproved_role_or_deployment_before_provider_call(
    workflows_agents_tables: None,
    settings: Any,
    monkeypatch: pytest.MonkeyPatch,
    rejection: str,
) -> None:
    """A configured policy fails closed on absent roles and identity mismatches."""

    del workflows_agents_tables
    from angee.workflows_agents.steps import InferStepImpl

    model = _inference_model(f"unapproved-{rejection}")
    identity = model.deployment_identity()
    if rejection == "role":
        settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {"mapping": [identity]}
    else:
        settings.ANGEE_INFERENCE_APPROVED_DEPLOYMENTS = {
            "classification": [{**identity, "endpoint": "https://different.invalid/v1"}]
        }
    bindings = _stub_model_backend(
        monkeypatch,
        lambda messages, info: ModelResponse(parts=[TextPart("must not run")]),
    )
    debits: list[dict[str, int]] = []
    step_run = SimpleNamespace(
        input=_infer_input(model),
        run=SimpleNamespace(debit_budget=lambda delta: debits.append(dict(delta))),
        _state=SimpleNamespace(adding=False, db="default"),
    )

    with pytest.raises(ValueError, match="policy is invalid|is not approved"):
        InferStepImpl().run(step_run, now=timezone.now())
    assert bindings == []
    assert debits == []


def test_infer_step_rejects_request_timeout_before_provider_error_routing(
    workflows_agents_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A validation message containing timeout never becomes a transient error."""

    del workflows_agents_tables
    from angee.workflows_agents.steps import InferStepImpl

    model = _inference_model("invalid-timeout")
    bindings = _stub_model_backend(
        monkeypatch,
        lambda messages, info: ModelResponse(parts=[TextPart("must not run")]),
    )
    value = _infer_input(model)
    value["request"]["settings"] = {"timeout": 2}
    debits: list[dict[str, int]] = []
    step_run = SimpleNamespace(
        input=value,
        run=SimpleNamespace(debit_budget=lambda delta: debits.append(dict(delta))),
        _state=SimpleNamespace(adding=False, db="default"),
    )

    with pytest.raises(PydanticValidationError, match="timeout belongs to the infer step input"):
        InferStepImpl().run(step_run, now=timezone.now())
    assert bindings == []
    assert debits == []


def test_infer_request_rejects_unknown_model_settings() -> None:
    """The native settings type does not admit undeclared transport knobs."""

    from angee.workflows_agents.steps import InferStepImpl

    value = _infer_input()
    value["request"]["settings"] = {"extra_query": {"model": "unchecked"}}

    with pytest.raises(PydanticValidationError, match="Unknown inference request settings: extra_query"):
        InferStepImpl.validate_input(value)


def test_infer_request_usage_is_a_workflow_budget_axis(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The normalized ``requests`` count participates in the generic run budget."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("infer-request-budget")
    _stub_model_backend(
        monkeypatch,
        lambda messages, info: ModelResponse(
            parts=[TextPart("done")],
            usage=RequestUsage(input_tokens=1, output_tokens=1),
        ),
    )
    workflow = _infer_workflow(budget={"requests": 0})
    run = _start_infer_run(workflow, _infer_input(model))

    advance_once(run)
    execute_started(run)
    engine.advance(run.pk)
    run.refresh_from_db()

    assert run.budget_spent == {
        "input_tokens": 1,
        "output_tokens": 1,
        "tokens": 2,
        "requests": 1,
    }
    assert run.status == workflow_models.RunStatus.FAILED
    assert "requests" in run.error


def test_replay_does_not_reinvoke_completed_infer_step(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replaying a completed inference activity reuses its retained output."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("infer-replay")
    calls: list[None] = []

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del messages, info
        calls.append(None)
        return ModelResponse(parts=[TextPart("done")])

    _stub_model_backend(monkeypatch, respond)
    run = _start_infer_run(_infer_workflow(), _infer_input(model))
    row = advance_once(run)[0]
    with system_context(reason="test workflows capture infer dispatch"):
        attempt = row.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)

    execute_started(run)
    engine.execute_dispatch(dispatch.pk, attempt.pk, attempt.lease_token)
    engine.advance(run.pk)
    engine.advance(run.pk)

    assert calls == [None]


def test_infer_terminal_provider_error_routes_failed_and_debits_once(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal provider error is retained and crosses the budget boundary once."""

    del workflows_agents_tables, no_workflow_queue
    model = _inference_model("infer-terminal")
    debits: list[dict[str, int]] = []

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise RuntimeError("backend unavailable")

    _stub_model_backend(monkeypatch, respond)
    monkeypatch.setattr(WorkflowRun, "debit_budget", lambda run, delta: debits.append(dict(delta)))
    workflow = workflow_with_steps(
        name="Inference terminal error",
        steps=(
            {
                "key": "infer",
                "step_class": "infer",
                "config": {},
                "input_binding": {"kind": "workflow_input", "path": []},
            },
            {"key": "on_failed", "step_class": "agent_session", "config": {"outcome": "done"}},
        ),
        edges=(("infer", "on_failed", "failed"),),
    )
    run = _start_infer_run(workflow, _infer_input(model))

    advance_once(run)
    execute_started(run)
    advance_once(run)

    infer_row = step_run_for(run, "infer")
    failed_row = step_run_for(run, "on_failed")
    assert infer_row.status == workflow_models.StepRunStatus.SUCCEEDED
    assert infer_row.outcome == "failed"
    assert infer_row.output == {
        "response": None,
        "usage": {},
        "error": {"type": "RuntimeError", "message": "backend unavailable"},
    }
    assert debits == [{}]
    assert failed_row.status == workflow_models.StepRunStatus.STARTED


def test_infer_error_after_response_debits_returned_usage_once(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A terminal projection error still debits the usage already returned by the model."""

    del workflows_agents_tables, no_workflow_queue
    from angee.workflows_agents import steps

    model = _inference_model("infer-projection-error")
    usage = {"input_tokens": 5, "output_tokens": 2, "tokens": 7, "requests": 1}
    debits: list[dict[str, int]] = []

    def fail_projection(response: ModelResponse) -> dict[str, Any]:
        del response
        raise TypeError("response projection failed")

    _stub_model_backend(
        monkeypatch,
        lambda messages, info: ModelResponse(
            parts=[TextPart("done")],
            usage=RequestUsage(input_tokens=5, output_tokens=2),
        ),
    )
    monkeypatch.setattr(steps, "_response_projection", fail_projection)
    monkeypatch.setattr(WorkflowRun, "debit_budget", lambda run, delta: debits.append(dict(delta)))
    run = _start_infer_run(_infer_workflow(), _infer_input(model))

    advance_once(run)
    execute_started(run)

    row = step_run_for(run, "infer")
    assert row.outcome == "failed"
    assert row.output == {
        "response": None,
        "usage": usage,
        "error": {"type": "TypeError", "message": "response projection failed"},
    }
    assert debits == [usage]


def test_infer_retryable_provider_error_allocates_retry_and_debits_once(
    workflows_agents_tables: None,
    no_workflow_queue: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shared provider classifier routes a 429 into the workflow retry policy."""

    del workflows_agents_tables, no_workflow_queue

    class RateLimitedError(Exception):
        status_code = 429

    model = _inference_model("infer-retryable")
    debits: list[dict[str, int]] = []

    def respond(messages: list[Any], info: AgentInfo) -> ModelResponse:
        del messages, info
        raise RateLimitedError("provider throttled")

    _stub_model_backend(monkeypatch, respond)
    monkeypatch.setattr(WorkflowRun, "debit_budget", lambda run, delta: debits.append(dict(delta)))
    run = _start_infer_run(_infer_workflow(retry={"max_attempts": 2}), _infer_input(model))
    step_run = advance_once(run)[0]

    execute_started(run)

    step_run.refresh_from_db()
    with system_context(reason="test retained transient result"):
        attempts = list(step_run.attempts.order_by("ordinal"))
    assert step_run.status == workflow_models.StepRunStatus.STARTED
    assert len(attempts) == 2
    assert attempts[0].result_kind == str(AttemptResultKind.TRANSIENT_ERROR)
    assert attempts[0].error == "provider throttled"
    assert attempts[1].retry_of_id == attempts[0].pk
    assert attempts[1].started_at is None
    assert debits == [{}]


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
    admit_workflow_actor(_session_workflow(), owner)
    session = sessions.start_session(agent, owner=owner, context={})
    with system_context(reason="test lost wakeup run"):
        run = sessions.run_for(session)
    assert run.origin == workflow_models.RunOrigin.SESSION
    step_run = advance_once(run)[0]
    original_run = AgentSessionStepImpl.run
    late_turns: list[Any] = []

    class FakeRunner:
        def run_turn(self, session: Any, turn: Any, **kwargs: Any) -> TurnOutcome:
            kwargs["heartbeat"]()
            kwargs["emit"]({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "caught"}})
            return TurnOutcome(kind="completed", text="caught", replay_state=[], usage={"requests": 1})

    monkeypatch.setattr(PydanticAIRuntime, "session_runner", lambda self: FakeRunner())

    def park_then_post(self: AgentSessionStepImpl, claimed: Any, *, now: Any) -> Any:
        result = original_run(self, claimed, now=now)
        if not late_turns:
            late_turns.append(sessions.post_message(session, "arrived during park"))
        return result

    monkeypatch.setattr(AgentSessionStepImpl, "run", park_then_post)
    execute_started(run)

    step_run.refresh_from_db()
    run.refresh_from_db()
    assert run.deliveries == 1
    assert step_run.claimed_deliveries == 0
    assert step_run.status == workflow_models.StepRunStatus.WAITING
    assert step_run.wait_until is not None and step_run.wait_until < timezone.now() + timedelta(seconds=1)

    monkeypatch.setattr(AgentSessionStepImpl, "run", original_run)
    assert engine.advance(run.pk) == {"claimed": 1}
    execute_started(run)

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
        steps=({"key": "quiet", "step_class": "agent_session", "config": {"outcome": "done"}},),
        edges=(),
    )
    run = start_run(workflow)
    step_run = advance_once(run, now=started_at)[0]
    with system_context(reason="test quiet heartbeat admit"):
        attempt = step_run.current_attempt
        dispatch = WorkflowDispatch.objects.get(step_attempt=attempt)
    with system_context(reason="test quiet heartbeat admit"), transaction.atomic():
        with WorkflowDispatch.objects._owner_transition(
            dispatch_id=dispatch.pk,
            lease_token=attempt.lease_token,
            at=started_at,
            using=WorkflowDispatch.objects.db,
        ) as preflight:
            StepAttempt.objects.admit_invocation(
                attempt.pk,
                lease_token=attempt.lease_token,
                at=started_at,
            )
            WorkflowDispatch.objects._consume_locked(
                dispatch.pk, envelope=preflight.envelope, at=started_at, alias="default"
            )
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


def test_generic_toolset_turn_keeps_outer_actor_and_async_db_boundary(
    workflows_agents_tables: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic toolset observes the runner's outer actor across an async ORM boundary."""

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
        type(agent),
        "inference_model",
        lambda selected: TestModel(call_tools=["builtin_read_owner"], custom_output_text="done"),
    )
    monkeypatch.setattr(
        runner_module,
        "toolsets_for_session",
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


def test_post_message_on_terminal_run_closes_session_and_refuses(
    workflows_agents_tables: None,
    no_workflow_queue: None,
) -> None:
    """A session whose run ended terminally self-heals to CLOSED on the next post.

    The UI reuses the latest non-CLOSED session; a run that died (cancel, reap,
    pre-exhaustion-fix failure) would otherwise pin an unusable session forever.
    """

    del workflows_agents_tables, no_workflow_queue

    owner, agent = _ready_session_agent("terminal-run")
    admit_workflow_actor(_session_workflow(), owner)
    session = sessions.start_session(agent, owner=owner, context={})
    with system_context(reason="test terminal run seed"):
        run = sessions.run_for(session)
        run.status_transitions.force_state(run, workflow_models.RunStatus.FAILED, reason="test terminal run")

    with pytest.raises(ValidationError, match="has ended"):
        sessions.post_message(session, "hello?")
    session.refresh_from_db()
    assert session.status == SessionStatus.CLOSED


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
    admit_workflow_actor(_session_workflow(), owner)
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
    execute_started(run)
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
    admit_workflow_actor(_session_workflow(), owner)

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


def _user_request(prompt: str) -> dict[str, Any]:
    """Return one JSON-native pydantic-ai request message."""

    return {"kind": "request", "parts": [{"part_kind": "user-prompt", "content": prompt}]}


def _infer_input(model: InferenceModel | None = None) -> dict[str, Any]:
    """Return the smallest valid retained input for the ``infer`` step."""

    return {
        "model": str(model.sqid) if model is not None else "unresolved-model-id",
        "role": "classification",
        "request": {"messages": [_user_request("Infer this document.")]},
        "timeout": 60,
    }


def _infer_workflow(
    *,
    budget: dict[str, Any] | None = None,
    retry: dict[str, Any] | None = None,
) -> Any:
    """Publish one workflow whose input is the complete inference envelope."""

    return workflow_with_steps(
        name="Inference fixture",
        budget=budget,
        steps=(
            {
                "key": "infer",
                "step_class": "infer",
                "config": {} if retry is None else {"retry": retry},
                "input_binding": {"kind": "workflow_input", "path": []},
            },
        ),
        edges=(),
    )


def _start_infer_run(workflow: Any, value: dict[str, Any]) -> Any:
    """Start one workflow with a present inference envelope."""

    return engine.start(workflow, subject=None, actor=admit_workflow_actor(workflow), input=JsonPresence(True, value))


def _inference_model(slug: str) -> InferenceModel:
    """Create one stub-backed inference model for workflow-agent tests."""

    provider = _provider(slug, backend_class="stub_inference", name="Stub provider")
    with system_context(reason="test workflows agent model setup"):
        return InferenceModel.objects.create(provider=provider, name=f"{slug}-model")


def _stub_model_backend(
    monkeypatch: pytest.MonkeyPatch,
    respond: Any,
) -> list[tuple[str, Any | None]]:
    """Stub only the backend binding while retaining the catalogue inference owner."""

    bindings: list[tuple[str, Any | None]] = []

    def model(
        backend: StubInferenceBackend,
        handle: str,
        *,
        credential: Any | None = None,
    ) -> FunctionModel:
        del backend
        bindings.append((handle, credential))
        return FunctionModel(respond, model_name=handle)

    monkeypatch.setattr(StubInferenceBackend, "model", model)
    return bindings


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
        key="agent_session",
        name="Agent session fixture",
        purpose=workflow_models.WorkflowPurpose.AGENT_SESSION,
        subject_declaration="agents.agentsession",
        max_steps=100000,
        steps=({"key": "session", "step_class": "agent_session", "config": {}},),
        edges=(),
    )
