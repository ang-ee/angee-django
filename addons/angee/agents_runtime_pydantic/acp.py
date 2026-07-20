"""Map pydantic-ai stream events into the ACP updates consumed by agents/web.

The persisted vocabulary is deliberately the exact subset reduced by
``agents/web/src/acp-log.ts`` and ``acp-session.ts``:

* text/thought chunks: ``{sessionUpdate, content: {type: "text", text}}``;
* tool calls: ``{sessionUpdate: "tool_call", toolCallId, title, status,
  rawInput}``;
* tool results: ``{sessionUpdate: "tool_call_update", toolCallId, status,
  rawOutput}``.

Keeping the mapping in this runtime addon lets a future container recorder emit
the same runtime-neutral transcript without teaching the agents models about
pydantic-ai event classes.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from pydantic_ai.messages import (
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
)


def updates_for_event(event: Any) -> Iterable[dict[str, Any]]:
    """Yield zero or more reducer-compatible ACP update payloads."""

    if isinstance(event, PartStartEvent):
        if isinstance(event.part, TextPart) and event.part.content:
            yield _text_update("agent_message_chunk", event.part.content)
        elif isinstance(event.part, ThinkingPart) and event.part.content:
            yield _text_update("agent_thought_chunk", event.part.content)
        elif isinstance(event.part, ToolCallPart):
            yield _tool_call(event.part)
    elif isinstance(event, PartDeltaEvent):
        if isinstance(event.delta, TextPartDelta) and event.delta.content_delta:
            yield _text_update("agent_message_chunk", event.delta.content_delta)
        elif isinstance(event.delta, ThinkingPartDelta) and event.delta.content_delta:
            yield _text_update("agent_thought_chunk", event.delta.content_delta)
    elif isinstance(event, FunctionToolCallEvent):
        yield _tool_call(event.part)
    elif isinstance(event, FunctionToolResultEvent):
        failed = isinstance(event.part, RetryPromptPart)
        yield {
            "sessionUpdate": "tool_call_update",
            "toolCallId": event.tool_call_id,
            "status": "failed" if failed else "completed",
            "rawOutput": _json_value(event.part.content),
        }


def approval_requests(requests: Any) -> list[dict[str, Any]]:
    """Return neutral decision payloads for pydantic-ai approval requests."""

    metadata = requests.metadata if isinstance(requests.metadata, dict) else {}
    return [
        {
            "tool_call_id": call.tool_call_id,
            "tool_name": call.tool_name,
            "args": _tool_args(call),
            "metadata": _json_value(metadata.get(call.tool_call_id, {})),
        }
        for call in requests.approvals
    ]


def _text_update(kind: str, text: str) -> dict[str, Any]:
    return {"sessionUpdate": kind, "content": {"type": "text", "text": text}}


def _tool_call(part: Any) -> dict[str, Any]:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": part.tool_call_id,
        "title": part.tool_name,
        "status": "pending",
        "rawInput": _tool_args(part),
    }


def _tool_args(part: Any) -> Any:
    args = getattr(part, "args", {})
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return args
    return _json_value(args)


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_value(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _json_value(model_dump(mode="json"))
    return str(value)
