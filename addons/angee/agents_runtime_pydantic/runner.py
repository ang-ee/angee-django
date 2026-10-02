"""pydantic-ai implementation of the runtime-neutral persisted session runner."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from time import monotonic
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from pydantic_ai import Agent, AgentRunResultEvent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import BinaryContent, ModelMessagesTypeAdapter
from pydantic_ai.models import Model
from pydantic_ai.usage import UsageLimits
from pydantic_core import to_jsonable_python
from rebac import SubjectRef, actor_context

from angee.agents.context import render_view_context
from angee.agents.models import normalize_inference_usage
from angee.agents.runners import SessionRunner, SessionUpdateSink, TurnOutcome
from angee.agents_runtime_pydantic.acp import approval_requests, updates_for_event
from angee.agents_runtime_pydantic.toolsets import toolsets_for_session
from angee.base.actors import user_subject_type

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
        deadline: float,
    ) -> TurnOutcome:
        """Render the view as its poster, then run as the ambient agent principal.

        AuditMixin retains the poster in ``turn.created_by_id``, including admin
        posts to another user's session. Unattributed turns add no view context.
        Native user history retains the rendered block at this turn.
        """

        history = ModelMessagesTypeAdapter.validate_python(session.replay_state or [])
        inference_model = session.agent.inference_model()
        toolsets = toolsets_for_session(session)
        limits = _usage_limits()
        deferred = _deferred_tool_results(deferred_results)
        prompt: str | list[str] | None = None
        if deferred is None:
            context = ""
            if turn.created_by_id is not None:
                with actor_context(SubjectRef.of(user_subject_type(), str(turn.created_by_id))):
                    context = render_view_context(dict(turn.context))
            # Native user content persists in all_messages(); instructions apply only to this run.
            prompt = [context, str(turn.prompt)] if context else str(turn.prompt)
        return async_to_sync(self._run_async)(
            prompt=prompt,
            history=history,
            deferred=deferred,
            instructions=session.agent.instructions.strip(),
            inference_model=inference_model,
            toolsets=toolsets,
            limits=limits,
            emit=emit,
            deadline=deadline,
        )

    async def _run_async(
        self,
        *,
        prompt: str | list[str] | None,
        history: list[Any],
        deferred: DeferredToolResults | None,
        instructions: str,
        inference_model: AbstractAsyncContextManager[Model],
        toolsets: list[Any],
        limits: UsageLimits,
        emit: SessionUpdateSink,
        deadline: float,
    ) -> TurnOutcome:
        async with asyncio.timeout(deadline - monotonic()), inference_model as model:
            agent = Agent(
                model=model,
                instructions=instructions,
                toolsets=toolsets,
                capabilities=[ToolSearch()],
                output_type=[str, DeferredToolRequests],
            )
            result = None
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
                        await sync_to_async(emit, thread_sensitive=True)(update)
            if result is None:
                raise RuntimeError("pydantic-ai completed without an AgentRunResultEvent.")

            replay_state = to_jsonable_python(_without_binary_content(result.all_messages()))
            usage = normalize_inference_usage(result.usage)
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
    """Project resolved tool decisions into pydantic-ai approvals."""

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


def _usage_limits() -> UsageLimits:
    """Bound model requests within one turn independently of context capacity."""

    return UsageLimits(request_limit=50)
