"""pydantic-ai implementation of the runtime-neutral persisted session runner."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter
from pydantic_ai.usage import UsageLimits
from pydantic_core import to_jsonable_python

from angee.agents.context import render_view_context
from angee.agents.runners import SessionHeartbeat, SessionRunner, SessionUpdateSink, TurnOutcome
from angee.agents_runtime_pydantic.acp import approval_requests, updates_for_event
from angee.agents_runtime_pydantic.providers import model_for_agent
from angee.agents_runtime_pydantic.toolsets import toolsets_for_session

_BINARY_CONTENT_OMITTED = "[Binary tool content omitted from persisted history; use a bounded file handle.]"
"""Replay-safe placeholder until the storage-handle follow-on lands."""


class PydanticAISessionRunner(SessionRunner):
    """Execute one bounded turn with pydantic-ai and return neutral state."""

    def run_turn(
        self,
        session: Any,
        turn: Any,
        *,
        deferred_results: list[Mapping[str, Any]],
        emit: SessionUpdateSink,
        heartbeat: SessionHeartbeat,
    ) -> TurnOutcome:
        """Bridge the workflow's synchronous task into pydantic-ai's async loop."""

        history = ModelMessagesTypeAdapter.validate_python(session.replay_state or [])
        context = render_view_context(dict(session.context or {}))
        instructions = "\n\n".join(part for part in (session.agent.instructions.strip(), context.strip()) if part)
        inference_model = model_for_agent(session.agent)
        toolsets = toolsets_for_session(session)
        limits = _usage_limits(session.agent)
        deferred = _deferred_tool_results(deferred_results)
        return async_to_sync(self._run_async)(
            prompt=None if deferred is not None else str(turn.prompt),
            history=history,
            deferred=deferred,
            instructions=instructions,
            inference_model=inference_model,
            toolsets=toolsets,
            limits=limits,
            emit=emit,
            heartbeat=heartbeat,
        )

    async def _run_async(
        self,
        *,
        prompt: str | None,
        history: list[Any],
        deferred: DeferredToolResults | None,
        instructions: str,
        inference_model: Any,
        toolsets: list[Any],
        limits: UsageLimits,
        emit: SessionUpdateSink,
        heartbeat: SessionHeartbeat,
    ) -> TurnOutcome:
        agent = Agent(
            model=inference_model,
            instructions=instructions,
            toolsets=toolsets,
            capabilities=[ToolSearch()],
            output_type=[str, DeferredToolRequests],
        )
        result = None
        heartbeat_task = asyncio.create_task(_heartbeat_loop(heartbeat))
        try:
            async with agent.run_stream_events(
                prompt,
                message_history=history,
                deferred_tool_results=deferred,
                usage_limits=limits,
            ) as events:
                async for event in events:
                    if isinstance(event, AgentRunResultEvent):
                        result = event.result
                        continue
                    for update in updates_for_event(event):
                        await database_sync_to_async(emit, thread_sensitive=True)(update)
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        if result is None:
            raise RuntimeError("pydantic-ai completed without an AgentRunResultEvent.")

        replay_state = to_jsonable_python(_without_binary_content(result.all_messages()))
        usage = _usage_delta(result.usage)
        if isinstance(result.output, DeferredToolRequests):
            requests = approval_requests(result.output)
            if not requests and result.output.calls:
                raise ValueError("External deferred tools are not supported by the pydantic runtime.")
            return TurnOutcome(
                kind="needs_approval",
                usage=usage,
                approval_requests=requests,
                replay_state=replay_state,
            )
        return TurnOutcome(
            kind="completed",
            usage=usage,
            text=str(result.output),
            replay_state=replay_state,
        )


def _deferred_tool_results(results: list[Mapping[str, Any]]) -> DeferredToolResults | None:
    """Project resolved workflow decisions into pydantic-ai approvals."""

    if not results:
        return None
    deferred = DeferredToolResults()
    for result in results:
        tool_call_id = str(result.get("tool_call_id") or "")
        if not tool_call_id:
            continue
        deferred.approvals[tool_call_id] = bool(result.get("approved"))
    return deferred


def _without_binary_content(value: Any) -> Any:
    """Copy a message tree with raw ``BinaryContent`` replaced by a handle marker.

    Binary bytes are never persisted in ``AgentSession.replay_state``. The
    storage-file handle design is a named follow-on; until then the persisted
    transcript records that content was omitted while the live turn may still
    show the bounded result to the model.
    """

    if isinstance(value, BinaryContent):
        return _BINARY_CONTENT_OMITTED
    if isinstance(value, Mapping):
        return {key: _without_binary_content(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_binary_content(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_without_binary_content(item) for item in value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        updates = {
            field.name: _without_binary_content(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.init
        }
        return dataclasses.replace(value, **updates)
    return value


def _usage_limits(agent: Any) -> UsageLimits:
    """Return a request-count guard; the workflow ledger owns token spend."""

    del agent
    return UsageLimits(request_limit=50)


async def _heartbeat_loop(heartbeat: SessionHeartbeat) -> None:
    """Refresh the workflow lease independently of model/tool emissions."""

    while True:
        await asyncio.sleep(60)
        await database_sync_to_async(heartbeat, thread_sensitive=True)()


def _usage_delta(usage: Any) -> dict[str, int]:
    """Normalize pydantic-ai RunUsage to the workflow budget vocabulary."""

    delta = {
        "input_tokens": int(usage.input_tokens or 0),
        "output_tokens": int(usage.output_tokens or 0),
        "requests": int(usage.requests or 0),
        "tool_calls": int(usage.tool_calls or 0),
    }
    delta = {key: value for key, value in delta.items() if value}
    total = int(usage.total_tokens or 0)
    if total:
        delta["tokens"] = total
    return delta
