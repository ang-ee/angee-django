"""Focused contracts for the in-process pydantic-ai runtime adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from time import monotonic
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from anthropic import AsyncAnthropic
from asgiref.sync import async_to_sync, sync_to_async
from django.contrib.auth import get_user_model
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets import ApprovalRequiredToolset
from pydantic_ai.toolsets.function import FunctionToolset
from rebac import (
    RelationshipTuple,
    actor_context,
    current_actor,
    system_context,
    to_object_ref,
    write_relationships,
)

from angee.agents.context import render_view_context
from angee.agents.models import AgentLifecycle, RuntimeStatus, SessionStatus, TurnStatus
from angee.agents.runtimes import ANTHROPIC_OAUTH_CLIENT_HEADERS, ANTHROPIC_OAUTH_SYSTEM_PREAMBLE
from angee.agents.tasks import run_session
from angee.agents.testing.models import Agent, AgentSession
from angee.agents_integrate_anthropic.backend import AnthropicInferenceBackend
from angee.agents_integrate_ollama.backend import OllamaInferenceBackend
from angee.agents_integrate_openai.backend import OpenAIInferenceBackend
from angee.agents_runtime_pydantic import runner as pydantic_runner
from angee.agents_runtime_pydantic.acp import updates_for_event
from angee.agents_runtime_pydantic.runner import PydanticAISessionRunner, _usage_limits
from angee.agents_runtime_pydantic.toolsets import _transport_for
from angee.integrate.credentials import CredentialKind
from tests.conftest import create_platform_admin


class _Credential:
    """Minimal credential double exercising the public SDK backend seam."""

    def __init__(self, value: str, *, kind: Any = CredentialKind.STATIC_TOKEN) -> None:
        self.value = value
        self.kind = kind
        self.freshened = 0

    def ensure_fresh(self) -> None:
        self.freshened += 1

    def secret_value(self) -> str:
        return self.value


def test_anthropic_model_keeps_override_and_oauth_beta_header(monkeypatch: Any) -> None:
    """Model binding resolves the override before async execution and closes its transport."""

    provider_credential = _Credential("provider-key")
    override = _Credential("agent-oauth", kind=CredentialKind.OAUTH)
    provider = SimpleNamespace(credential=provider_credential, base_url="https://anthropic.example/", config={})
    captured: list[dict[str, Any]] = []
    clients: list[AsyncAnthropic] = []

    def client_class(**kwargs: Any) -> AsyncAnthropic:
        captured.append(kwargs)
        client = AsyncAnthropic(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(AnthropicInferenceBackend, "_async_client_class", lambda self: client_class)
    binding = AnthropicInferenceBackend(provider).model("claude-sonnet-4-6", credential=override)
    assert override.freshened == 1
    assert provider_credential.freshened == 0

    async def inspect_model():
        async with binding as model:
            assert isinstance(model, AnthropicModel)
            http_client = captured[0]["http_client"]
            request = http_client.build_request(
                "POST",
                "https://anthropic.example/v1/messages",
                json={"system": "Follow the user's instructions.", "messages": []},
            )
            for hook in http_client.event_hooks["request"]:
                await hook(request)
            content = b"".join([chunk async for chunk in request.stream])
            assert json.loads(content)["system"] == [
                {"type": "text", "text": ANTHROPIC_OAUTH_SYSTEM_PREAMBLE},
                {"type": "text", "text": "Follow the user's instructions."},
            ]
            assert request.headers["content-length"] == str(len(content))

    async_to_sync(inspect_model)()
    assert len(captured) == 1
    http_client = captured[0].pop("http_client")
    assert http_client.is_closed
    assert clients[0].is_closed()
    assert captured == [
        {
            "auth_token": "agent-oauth",
            "base_url": "https://anthropic.example",
            "default_headers": dict(ANTHROPIC_OAUTH_CLIENT_HEADERS),
        }
    ]


def test_openai_model_keeps_per_agent_static_credential(monkeypatch: Any) -> None:
    """Model binding forwards the explicit agent credential and closes the SDK client."""

    provider_credential = _Credential("provider-key")
    override = _Credential("agent-key")
    provider = SimpleNamespace(credential=provider_credential, base_url="", config={"timeout_seconds": 12})
    captured: list[dict[str, Any]] = []
    clients: list[AsyncOpenAI] = []

    def client_class(**kwargs: Any) -> AsyncOpenAI:
        captured.append(kwargs)
        client = AsyncOpenAI(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(OpenAIInferenceBackend, "_async_client_class", lambda self: client_class)
    binding = OpenAIInferenceBackend(provider).model("gpt-4.1", credential=override)
    assert override.freshened == 1
    assert provider_credential.freshened == 0

    async def inspect_model():
        async with binding as model:
            assert isinstance(model, OpenAIChatModel)

    async_to_sync(inspect_model)()
    assert captured == [{"api_key": "agent-key", "timeout": 12}]
    assert clients[0].is_closed()


def test_backend_binds_ollama_without_a_credential(monkeypatch: Any) -> None:
    """The declared OpenAI protocol builds Ollama with its no-auth endpoint defaults."""

    captured: list[dict[str, Any]] = []
    clients: list[AsyncOpenAI] = []

    def client_class(**kwargs: Any) -> AsyncOpenAI:
        captured.append(kwargs)
        client = AsyncOpenAI(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(OllamaInferenceBackend, "_async_client_class", lambda self: client_class)
    provider = SimpleNamespace(credential=None, base_url="", config={})
    provider.backend = OllamaInferenceBackend(provider)

    async def inspect_model():
        async with provider.backend.model("llama3.2:latest") as model:
            assert isinstance(model, OpenAIChatModel)

    async_to_sync(inspect_model)()

    assert len(captured) == 1
    http_client = captured[0].pop("http_client")
    assert isinstance(http_client, DefaultAsyncHttpxClient)
    assert http_client.trust_env is False
    assert http_client.follow_redirects is False
    assert http_client.is_closed
    assert clients[0].is_closed()
    assert captured == [
        {
            "api_key": "not-required",
            "base_url": "http://localhost:11434/v1",
        }
    ]


def test_acp_events_emit_reducer_shapes_and_one_tool_call() -> None:
    """Pydantic stream events map to exactly the shapes reduced by agents/web."""

    call = ToolCallPart(tool_name="read_note", args='{"id":"nte_1"}', tool_call_id="call-1")
    events = [
        PartStartEvent(index=0, part=TextPart("Hello")),
        PartDeltaEvent(index=0, delta=TextPartDelta(" world")),
        PartStartEvent(index=1, part=call),
        FunctionToolCallEvent(call),
        FunctionToolResultEvent(
            ToolReturnPart(tool_name="read_note", content={"title": "Note"}, tool_call_id="call-1")
        ),
    ]

    updates = [update for event in events for update in updates_for_event(event)]

    assert updates == [
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello"}},
        {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " world"}},
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-1",
            "title": "read_note",
            "status": "pending",
            "rawInput": {"id": "nte_1"},
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "title": "read_note",
            "status": "in_progress",
            "rawInput": {"id": "nte_1"},
        },
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "call-1",
            "status": "completed",
            "rawOutput": {"title": "Note"},
        },
    ]
    assert sum(update["sessionUpdate"] == "tool_call" for update in updates) == 1


def test_external_mcp_transport_carries_its_agent_bearer() -> None:
    """External servers retain the authenticated HTTP transport."""

    credential = _Credential("agent-bearer")
    server = SimpleNamespace(
        name="Angee",
        resolved_url="http://angee.test/mcp",
        builtin="",
        credential_id=1,
        credential=credential,
    )

    transport = _transport_for(server)

    assert transport.url == "http://angee.test/mcp"
    assert transport.headers == {"Authorization": "Bearer agent-bearer"}
    assert credential.freshened == 1


def test_usage_limits_allow_a_multi_request_tool_turn(db: Any) -> None:
    """A tool turn may make multiple model requests without per-turn token limits."""

    del db
    calls: list[int] = []

    async def lookup(value: int = 1) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    limits = _usage_limits()
    outcome = async_to_sync(PydanticAISessionRunner()._run_async)(
        prompt="Use the lookup tool.",
        history=[],
        deferred=None,
        instructions="",
        inference_model=TestModel(call_tools=["lookup"], custom_output_text="done"),
        toolsets=[FunctionToolset([lookup])],
        limits=limits,
        emit=lambda update: None,
        deadline=monotonic() + 30,
    )

    assert limits.request_limit == 50
    assert limits.input_tokens_limit is None
    assert limits.output_tokens_limit is None
    assert limits.total_tokens_limit is None
    assert calls == [1]
    assert outcome.kind == "completed"
    assert outcome.text == "done"
    assert outcome.usage["requests"] == 2
    assert not isinstance(outcome.replay_state, DeferredToolRequests)


def test_view_context_persists_in_native_history_at_its_turn(
    composed_tables: None,
    capture_tasks: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumed view attachment remains in history; a later view follows it."""

    del composed_tables, capture_tasks
    owner = get_user_model().objects.create_user(username="context-history-owner")
    with system_context(reason="test context history agent"):
        agent = Agent.objects.create(
            name="Context history agent",
            owner=owner,
            runtime_class="pydantic",
            instructions="Answer the user's question.",
            lifecycle=AgentLifecycle.READY,
            runtime_status=RuntimeStatus.RUNNING,
        )
        principal = agent.principal_subject()
    with actor_context(principal):
        assert not Agent.objects.filter(pk=agent.pk).exists()
    requests: list[list[ModelMessage]] = []
    request_stream = TestModel.request_stream

    @asynccontextmanager
    async def inspect_request(
        model: TestModel,
        messages: list[ModelMessage],
        *args: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        assert current_actor() == principal
        requests.append(deepcopy(messages))
        async with request_stream(model, messages, *args, **kwargs) as response:
            yield response

    monkeypatch.setattr(TestModel, "request_stream", inspect_request)
    monkeypatch.setattr(Agent, "inference_model", lambda selected: TestModel(custom_output_text="Reply"))
    monkeypatch.setattr(pydantic_runner, "toolsets_for_session", lambda selected: [])
    contexts = [
        {"kind": "record", "type": "agents/agent", "sqid": str(agent.sqid)},
        {},
        {"kind": "list", "type": "agents/agent", "params": {"search": "Another view"}},
    ]
    prompts = ["First question", "Follow up", "Another question"]
    with actor_context(owner):
        session = AgentSession.objects.start(
            agent,
            owner=owner,
            context={"kind": "list", "type": "agents/agent", "params": {"search": "Session origin"}},
        )
        expected: list[str | list[str]] = []
        for index, (prompt, context) in enumerate(zip(prompts, contexts, strict=True)):
            block = render_view_context(context)
            if block:
                assert agent.name in block
            expected.append([block, prompt] if block else prompt)
            turn = session.post(prompt, context=context)
            run_session.run(session.pk)
            turn.refresh_from_db()
            session.refresh_from_db()

            assert turn.status == TurnStatus.COMPLETED
            assert turn.prompt == prompt
            assert session.title == prompts[0]
            assert len(requests) == index + 1
            sent = [message for message in requests[index] if isinstance(message, ModelRequest)]
            assert [
                part.content for message in sent for part in message.parts if isinstance(part, UserPromptPart)
            ] == expected
            assert all(message.instructions == agent.instructions for message in sent)
            retained = ModelMessagesTypeAdapter.validate_python(session.replay_state)
            assert [
                part.content
                for message in retained
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, UserPromptPart)
            ] == expected


@pytest.mark.parametrize(
    ("poster_kind", "poster_can_read"),
    [("owner", True), ("owner", False), ("admin", True), ("unattributed", True)],
)
def test_view_context_uses_the_poster_read_scope_and_tools_stay_agent_scoped(
    composed_tables: None,
    capture_tasks: list[Any],
    monkeypatch: pytest.MonkeyPatch,
    poster_kind: str,
    poster_can_read: bool,
) -> None:
    """Only the render step uses the poster; native tools retain the agent's reach."""

    del composed_tables, capture_tasks
    owner = get_user_model().objects.create_user(username="render-owner")
    stranger = get_user_model().objects.create_user(username="record-owner")
    poster = create_platform_admin("render-admin") if poster_kind == "admin" else owner
    with system_context(reason="test view context actors"):
        agent = Agent.objects.create(
            name="Rendering agent",
            owner=owner,
            runtime_class="pydantic",
            lifecycle=AgentLifecycle.READY,
            runtime_status=RuntimeStatus.RUNNING,
        )
        principal = agent.principal_subject()
        record = Agent.objects.create(
            name="Attached record contents",
            owner=owner if poster_kind != "admin" and poster_can_read else stranger,
        )
        if not poster_can_read:
            write_relationships(
                [RelationshipTuple(resource=to_object_ref(record), relation="reader", subject=principal)]
            )
    with actor_context(poster):
        assert Agent.objects.filter(pk=record.pk).exists() == poster_can_read
    with actor_context(owner):
        if poster_kind == "admin":
            assert not Agent.objects.filter(pk=record.pk).exists()
        session = AgentSession.objects.start(agent, owner=owner, context={})
        turn = session.post(
            "Read the attached record.",
            context={"kind": "record", "type": "agents/agent", "sqid": str(record.sqid)},
            actor=poster,
        )
    assert turn.created_by_id == poster.pk
    if poster_kind == "unattributed":
        with system_context(reason="test unattributed historical turn"):
            type(turn).objects.filter(pk=turn.pk).update(created_by=None)
    calls: list[tuple[Any, list[str]]] = []

    def read_record() -> list[str]:
        rows = list(Agent.objects.filter(pk=record.pk).values_list("name", flat=True))
        calls.append((current_actor(), rows))
        return rows

    async def read_attached_record() -> list[str]:
        """Read the attached record with the tool caller's permissions."""
        return await sync_to_async(read_record, thread_sensitive=True)()

    def inference_model(selected: Any) -> TestModel:
        assert current_actor() == principal
        return TestModel(call_tools=["read_attached_record"], custom_output_text="Reply")

    def toolsets(selected: Any) -> list[FunctionToolset]:
        assert current_actor() == principal
        return [FunctionToolset([read_attached_record])]

    monkeypatch.setattr(Agent, "inference_model", inference_model)
    monkeypatch.setattr(pydantic_runner, "toolsets_for_session", toolsets)
    prior_actor = current_actor()
    run_session.run(session.pk)
    assert current_actor() == prior_actor
    with actor_context(owner):
        turn.refresh_from_db()
        session.refresh_from_db()
    assert turn.status == TurnStatus.COMPLETED
    assert turn.created_by_id == (None if poster_kind == "unattributed" else poster.pk)
    assert calls == [(principal, [] if poster_can_read else [record.name])]
    retained = ModelMessagesTypeAdapter.validate_python(session.replay_state)
    prompts = [
        part.content
        for message in retained
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    if poster_kind == "unattributed":
        assert prompts == [turn.prompt]
    else:
        assert len(prompts) == 1
        block, text = prompts[0]
        assert text == turn.prompt
        assert (record.name in block) == poster_can_read
        assert ("Selected rows (1):" in block) == poster_can_read
        assert ("No rows selected." in block) != poster_can_read


def test_stream_sink_can_stop_the_native_model_loop() -> None:
    """The orchestration sink's stop exception reaches its caller unchanged."""

    stopped = RuntimeError("Stop requested.")

    def stop(update: dict[str, Any]) -> None:
        raise stopped

    with pytest.raises(RuntimeError) as raised:
        async_to_sync(PydanticAISessionRunner()._run_async)(
            prompt="Return a streamed reply.",
            history=[],
            deferred=None,
            instructions="",
            inference_model=TestModel(custom_output_text="A streamed reply."),
            toolsets=[],
            limits=_usage_limits(),
            emit=stop,
            deadline=monotonic() + 30,
        )

    assert raised.value is stopped


def test_unsupported_approval_does_not_poison_native_model_history(
    composed_tables: None,
    capture_tasks: list[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del composed_tables, capture_tasks
    owner = get_user_model().objects.create_user(username="native-session-owner")
    with system_context(reason="test native agent session seed"):
        agent = Agent.objects.create(
            name="Native session agent",
            owner=owner,
            runtime_class="pydantic",
            lifecycle=AgentLifecycle.READY,
            runtime_status=RuntimeStatus.RUNNING,
        )
    calls: list[str] = []

    def read_document() -> str:
        """Read a document after approval."""
        calls.append("read")
        return "Document"

    monkeypatch.setattr(
        pydantic_runner,
        "toolsets_for_session",
        lambda selected: [ApprovalRequiredToolset(FunctionToolset([read_document]))],
    )
    monkeypatch.setattr(Agent, "inference_model", lambda selected: TestModel(call_tools=["read_document"]))
    with actor_context(owner):
        session = AgentSession.objects.start(agent, owner=owner, context={})
        prior_replay = session.replay_state
        approval = session.post("Read the document")
        run_session.run(session.pk)
        approval.refresh_from_db()
        session.refresh_from_db()
        assert approval.error == "Tool approvals are not available yet."
        assert session.replay_state == prior_replay
        assert calls == []

        monkeypatch.setattr(
            Agent,
            "inference_model",
            lambda selected: TestModel(call_tools=[], custom_output_text="After"),
        )
        continued = session.post("Continue without the tool")
        run_session.run(session.pk)
        continued.refresh_from_db()
        session.refresh_from_db()
        assert continued.status == TurnStatus.COMPLETED
        assert continued.text == "After"
        assert session.status == SessionStatus.IDLE


@pytest.mark.parametrize("expires", [False, True])
def test_stream_cancellation_and_deadline_close_sdk_client(monkeypatch: Any, expires: bool) -> None:
    """Cancellation and the task deadline close a waiting native SDK transport."""

    clients: list[AsyncOpenAI] = []

    async def cancel_request() -> None:
        request_started = asyncio.Event()
        pending = asyncio.Event()

        async def respond(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/chat/completions")
            request_started.set()
            await pending.wait()
            raise AssertionError("The test must cancel the pending model request.")

        def client_class(**kwargs: Any) -> AsyncOpenAI:
            client = AsyncOpenAI(
                **kwargs,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
                max_retries=0,
            )
            clients.append(client)
            return client

        monkeypatch.setattr(OpenAIInferenceBackend, "_async_client_class", lambda self: client_class)
        provider = SimpleNamespace(
            credential=_Credential("test-key"), base_url="https://provider.invalid/v1", config={}
        )
        task = asyncio.create_task(
            PydanticAISessionRunner()._run_async(
                prompt="Keep the request pending.",
                history=[],
                deferred=None,
                instructions="",
                inference_model=OpenAIInferenceBackend(provider).model("gpt-4.1"),
                toolsets=[],
                limits=_usage_limits(),
                emit=lambda update: None,
                deadline=monotonic() + (0.2 if expires else 30),
            )
        )
        try:
            await asyncio.wait_for(request_started.wait(), timeout=5)
            if expires:
                with pytest.raises(TimeoutError):
                    await task
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert len(clients) == 1 and clients[0].is_closed()

    async_to_sync(cancel_request)()
