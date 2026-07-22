"""Focused contracts for the in-process pydantic-ai runtime adapter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from asgiref.sync import async_to_sync
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.function import FunctionToolset

from angee.agents.runtimes import ANTHROPIC_OAUTH_CLIENT_HEADERS
from angee.agents_integrate_anthropic.backend import (
    AnthropicInferenceBackend,
    _OAuthMessagesTransport,
)
from angee.agents_integrate_ollama.backend import OllamaInferenceBackend
from angee.agents_integrate_openai.backend import OpenAIInferenceBackend
from angee.agents_runtime_pydantic.acp import updates_for_event
from angee.agents_runtime_pydantic.providers import model_for_agent
from angee.agents_runtime_pydantic.runner import PydanticAISessionRunner, _usage_limits
from angee.agents_runtime_pydantic.toolsets import _transport_for
from angee.integrate.credentials import CredentialKind


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


def test_anthropic_async_client_keeps_override_and_oauth_beta_header(monkeypatch: Any) -> None:
    """The async Anthropic client accepts per-agent credentials without dropping OAuth policy."""

    provider_credential = _Credential("provider-key")
    override = _Credential("agent-oauth", kind=CredentialKind.OAUTH)
    provider = SimpleNamespace(credential=provider_credential, base_url="https://anthropic.example/", config={})
    captured: list[dict[str, Any]] = []

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr("angee.agents.sdk_backends.import_string", lambda path: FakeAsyncAnthropic)

    AnthropicInferenceBackend(provider).async_client(credential=override)

    assert len(captured) == 1
    http_client = captured[0].pop("http_client")
    assert isinstance(http_client._transport, _OAuthMessagesTransport)
    assert captured == [
        {
            "auth_token": "agent-oauth",
            "base_url": "https://anthropic.example",
            "default_headers": dict(ANTHROPIC_OAUTH_CLIENT_HEADERS),
        }
    ]
    assert override.freshened == 1
    assert provider_credential.freshened == 0


def test_openai_async_client_keeps_per_agent_static_credential(monkeypatch: Any) -> None:
    """The OpenAI async-client mirror forwards the explicit agent credential."""

    provider_credential = _Credential("provider-key")
    override = _Credential("agent-key")
    provider = SimpleNamespace(credential=provider_credential, base_url="", config={"timeout_seconds": 12})
    captured: list[dict[str, Any]] = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)

    monkeypatch.setattr("angee.agents.sdk_backends.import_string", lambda path: FakeAsyncOpenAI)

    OpenAIInferenceBackend(provider).async_client(credential=override)

    assert captured == [{"api_key": "agent-key", "timeout": 12}]
    assert override.freshened == 1
    assert provider_credential.freshened == 0


def test_model_for_agent_builds_ollama_without_a_credential(monkeypatch: Any) -> None:
    """The declared OpenAI protocol builds Ollama with its no-auth endpoint defaults."""

    captured: list[dict[str, Any]] = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(kwargs)
            self.base_url = kwargs["base_url"]

    monkeypatch.setattr("angee.agents.sdk_backends.import_string", lambda path: FakeAsyncOpenAI)
    provider = SimpleNamespace(credential=None, base_url="", config={})
    provider.backend = OllamaInferenceBackend(provider)
    agent = SimpleNamespace(
        model=SimpleNamespace(provider=provider, provider_model_name="llama3.2:latest"),
        inference_credential_for_runtime=lambda: None,
    )

    inference_model = model_for_agent(agent)

    assert isinstance(inference_model, OpenAIChatModel)
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
    """A tool turn may make multiple model requests; the workflow ledger owns token limits."""

    del db
    calls: list[int] = []

    async def lookup(value: int = 1) -> dict[str, int]:
        calls.append(value)
        return {"value": value}

    limits = _usage_limits(SimpleNamespace(model=SimpleNamespace(context_window=1, max_output_tokens=1)))
    outcome = async_to_sync(PydanticAISessionRunner()._run_async)(
        prompt="Use the lookup tool.",
        history=[],
        deferred=None,
        instructions="",
        inference_model=TestModel(call_tools=["lookup"], custom_output_text="done"),
        toolsets=[FunctionToolset([lookup])],
        limits=limits,
        emit=lambda update: None,
        heartbeat=lambda: None,
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
