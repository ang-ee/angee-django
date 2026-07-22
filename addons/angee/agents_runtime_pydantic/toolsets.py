"""Compose REBAC-gated native and remote toolsets for one agent session.

Built-in Angee tools execute directly through their registered FastMCP
``Tool.run`` implementation. FastMCP remains the registration/compiler owner;
the in-process adapter only translates its definitions and results into
pydantic-ai's toolset contract. Remote servers keep pydantic-ai's MCP transport.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool as FastMCPTool
from fastmcp.tools import ToolResult
from fastmcp.tools.function_tool import FunctionTool
from mcp.types import TextContent
from pydantic import ValidationError
from pydantic_ai.exceptions import ModelRetry
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    FilteredToolset,
    ToolsetTool,
)
from pydantic_ai.toolsets.wrapper import WrapperToolset
from pydantic_core import SchemaValidator, core_schema
from rebac import PermissionDenied, SubjectRef, actor_context, to_subject_ref
from rebac.backends import backend

from angee.agents.grants import TOOL_GRANT_RESOURCE_TYPE, tool_grant_ref
from angee.agents.models import BUILTIN_MCP_ANGEE
from angee.mcp.server import mcp_server

MAX_TOOL_RESULT_CHARS = 32_000
"""Maximum inline characters returned from one in-process tool call."""

TOOL_RESULT_TRUNCATED = "\n[Tool result truncated. Narrow your query and try again.]"
"""Marker appended when an in-process tool result exceeds the inline ceiling."""

ALWAYS_LOADED_TOOLS = frozenset({"list_resources"})
"""Small native core kept visible beside pydantic-ai's own ``search_tools``."""

_TOOL_ARGS_VALIDATOR = SchemaValidator(
    schema=core_schema.dict_schema(core_schema.str_schema(), core_schema.any_schema())
)


@dataclass
class ToolGrantAccess:
    """Share one advertisement lookup and fresh per-call checks across toolsets."""

    agent: SubjectRef
    _granted_task: asyncio.Task[frozenset[str]] | None = field(default=None, init=False, repr=False)

    async def granted_names(self) -> frozenset[str]:
        """Return ``use``-accessible grant ids through exactly one backend call."""

        if self._granted_task is None:
            self._granted_task = asyncio.create_task(
                sync_to_async(_accessible_tool_names, thread_sensitive=True)(self.agent)
            )
        return await self._granted_task

    async def check(self, tool_name: str) -> None:
        """Re-gate one invocation so revocation beats stale advertisement."""

        allowed = await sync_to_async(_tool_access_allowed, thread_sensitive=True)(
            self.agent,
            tool_name,
        )
        if not allowed:
            raise PermissionDenied(f"Agent may not use tool {tool_name!r}.")


@dataclass(kw_only=True)
class _AngeeToolsetTool(ToolsetTool[Any]):
    """pydantic-ai definition paired with its registered FastMCP implementation."""

    registered_tool: FastMCPTool
    writes: bool


@dataclass
class AngeeToolset(AbstractToolset[Any]):
    """Execute granted built-in FastMCP tools natively for one persisted session."""

    session: Any
    access: ToolGrantAccess

    @property
    def id(self) -> str:
        """Return the stable id of the process-native toolset."""

        return BUILTIN_MCP_ANGEE

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Advertise the one accessible grant set intersected with the live registry."""

        granted = await self.access.granted_names()
        server = await sync_to_async(mcp_server, thread_sensitive=True)()
        tools: dict[str, ToolsetTool[Any]] = {}
        for name in sorted(granted):
            registered = await server.get_tool(name)
            if registered is None:
                continue
            _assert_in_process_compatible(registered)
            annotations = registered.annotations
            assert annotations is not None and annotations.readOnlyHint is not None
            tools[name] = _AngeeToolsetTool(
                toolset=self,
                tool_def=ToolDefinition(
                    name=name,
                    description=registered.description,
                    parameters_json_schema=registered.parameters,
                    return_schema=registered.output_schema,
                    defer_loading=name not in ALWAYS_LOADED_TOOLS,
                    metadata={
                        "annotations": annotations.model_dump(mode="json", by_alias=True, exclude_none=True),
                    },
                ),
                max_retries=ctx.max_retries,
                args_validator=_TOOL_ARGS_VALIDATOR,
                registered_tool=registered,
                writes=annotations.readOnlyHint is False,
            )
        return tools

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: ToolsetTool[Any],
    ) -> Any:
        """Re-gate, select the execution actor, run directly, and bound the result."""

        del ctx
        if not isinstance(tool, _AngeeToolsetTool):
            raise TypeError("AngeeToolset received a tool it did not advertise.")
        try:
            await self.access.check(name)
        except PermissionDenied as error:
            raise ModelRetry("You no longer have permission to use this tool.") from error

        actor = self.access.agent if tool.writes else to_subject_ref(self.session.owner)
        try:
            with actor_context(actor):
                result = await tool.registered_tool.run(tool_args)
        except ValidationError as error:
            raise ModelRetry("Invalid tool arguments. Check the tool schema and try again.") from error
        except ToolError as error:
            raise ModelRetry(str(error)) from error
        except Exception as error:
            raise ModelRetry("The tool could not be completed.") from error
        return _bounded_tool_result(_tool_result_value(result))


@dataclass
class ToolGrantToolset(WrapperToolset[Any]):
    """Apply the same REBAC grant gate to an external MCP toolset."""

    access: ToolGrantAccess

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Filter remote advertisement through the shared ``use`` grant set."""

        granted = await self.access.granted_names()
        tools = await self.wrapped.get_tools(ctx)
        return {name: tool for name, tool in tools.items() if name in granted}

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: ToolsetTool[Any],
    ) -> Any:
        """Re-gate immediately before delegating to the remote MCP transport."""

        try:
            await self.access.check(name)
        except PermissionDenied as error:
            raise ModelRetry("You no longer have permission to use this tool.") from error
        return await self.wrapped.call_tool(name, tool_args, ctx, tool)


def toolsets_for_session(session: Any) -> list[Any]:
    """Return native plus remote toolsets authorized for ``session``."""

    agent = session.agent
    selected: dict[Any, dict[str, bool]] = defaultdict(dict)
    for tool in agent.mcp_tools.select_related("server"):
        if tool.enabled:
            selected[tool.server_id][tool.name] = bool(tool.requires_approval)

    access = ToolGrantAccess(agent.principal_subject())
    native: Any = AngeeToolset(session, access)
    native_approvals = frozenset(name for tools in selected.values() for name, required in tools.items() if required)
    if native_approvals:
        native = ApprovalRequiredToolset(
            native,
            approval_required_func=_approval_filter(native_approvals),
        )

    toolsets: list[Any] = [native]
    for server in agent.mcp_servers.select_related("credential").order_by("name"):
        if server.builtin == BUILTIN_MCP_ANGEE:
            continue
        tools = selected.get(server.pk, {})
        if not tools:
            continue
        transport = _transport_for(server)
        toolset: Any = MCPToolset(transport, id=str(server.sqid))
        toolset = FilteredToolset(toolset, filter_func=_tool_filter(frozenset(tools)))
        toolset = ToolGrantToolset(toolset, access)
        approvals = frozenset(name for name, required in tools.items() if required)
        if approvals:
            toolset = ApprovalRequiredToolset(
                toolset,
                approval_required_func=_approval_filter(approvals),
            )
        toolsets.append(toolset)
    return toolsets


def _accessible_tool_names(agent: SubjectRef) -> frozenset[str]:
    """Read all directly or indirectly granted tool ids for one agent principal."""

    return frozenset(
        backend().accessible(
            subject=agent,
            action="use",
            resource_type=TOOL_GRANT_RESOURCE_TYPE,
        )
    )


def _tool_access_allowed(agent: SubjectRef, tool_name: str) -> bool:
    """Return the fresh per-call ``use`` decision for one canonical grant ref."""

    return (
        backend()
        .check_access(
            subject=agent,
            action="use",
            resource=tool_grant_ref(tool_name),
        )
        .allowed
    )


def _assert_in_process_compatible(tool: FastMCPTool) -> None:
    """Assert a built-in tool declares actor posture and no request-only context.

    ``Tool.run`` is invoked outside a FastMCP request, so ``Context`` injection and
    ``get_access_token()`` cannot work here. Registration authors must instead use
    the ambient REBAC actor and declare ``ToolAnnotations.readOnlyHint``; the latter
    selects owner impersonation (read) versus agent attribution (write).
    """

    if tool.annotations is None or tool.annotations.readOnlyHint is None:
        raise ImproperlyConfigured(f"Built-in MCP tool {tool.name!r} must declare ToolAnnotations.readOnlyHint.")
    if not isinstance(tool, FunctionTool):
        return
    signature = inspect.signature(tool.fn)
    for parameter in signature.parameters.values():
        if (
            getattr(parameter.annotation, "__name__", "") == "Context"
            or type(parameter.default).__name__ == "_CurrentContext"
        ):
            raise ImproperlyConfigured(f"Built-in MCP tool {tool.name!r} depends on request-scoped FastMCP Context.")
    target = inspect.unwrap(tool.fn)
    code = getattr(target, "__code__", None)
    if code is not None and "get_access_token" in code.co_names:
        raise ImproperlyConfigured(f"Built-in MCP tool {tool.name!r} calls request-scoped get_access_token().")


def _tool_result_value(result: ToolResult) -> Any:
    """Map one FastMCP result to pydantic-ai's model-visible return value."""

    if result.is_error:
        raise ToolError("The tool reported an error.")
    text_parts = [part for part in result.content if isinstance(part, TextContent)]
    text_only = len(text_parts) == len(result.content)
    if result.structured_content is not None and text_only:
        structured = result.structured_content
        if len(structured) == 1 and "result" in structured:
            return structured["result"]
        return structured
    if text_only:
        text = "\n".join(part.text for part in text_parts)
        return text
    return [part.model_dump(mode="json", by_alias=True, exclude_none=True) for part in result.content]


def _bounded_tool_result(value: Any) -> Any:
    """Return ``value`` inline when bounded, otherwise a truncated JSON preview."""

    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    if len(serialized) <= MAX_TOOL_RESULT_CHARS:
        return value
    preview_length = MAX_TOOL_RESULT_CHARS - len(TOOL_RESULT_TRUNCATED)
    return f"{serialized[:preview_length]}{TOOL_RESULT_TRUNCATED}"


def _transport_for(server: Any) -> Any:
    """Return the owner-native HTTP transport for one external MCP server row."""

    url = str(server.resolved_url or "").strip()
    if not url:
        raise ValueError(f"MCP server {server.name!r} has no addressable URL.")
    headers: dict[str, str] = {}
    if server.credential_id:
        server.credential.ensure_fresh()
        bearer = str(server.credential.secret_value() or "")
        if not bearer:
            raise ValueError(f"MCP server {server.name!r} has an empty credential.")
        headers["Authorization"] = f"Bearer {bearer}"
    return StreamableHttpTransport(url, headers=headers or None)


def _tool_filter(names: frozenset[str]) -> Callable[[Any, Any], bool]:
    """Return a typed pydantic-ai tool-definition filter."""

    def includes(_context: Any, definition: Any) -> bool:
        return bool(definition.name in names)

    return includes


def _approval_filter(names: frozenset[str]) -> Callable[[Any, Any, Any], bool]:
    """Return a typed approval predicate for selected tool names."""

    def requires_approval(_context: Any, definition: Any, _args: Any) -> bool:
        return bool(definition.name in names)

    return requires_approval
