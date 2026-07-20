"""Project an agent's selected MCP rows into pydantic-ai toolsets."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import ApprovalRequiredToolset, FilteredToolset


def toolsets_for_agent(agent: Any) -> list[Any]:
    """Return MCP toolsets narrowed to the tools explicitly selected by ``agent``.

    Every server, including the built-in Angee server, uses authenticated
    Streamable HTTP. FastMCP's pinned in-memory transport has no bearer channel,
    so using it would make actor resolution depend on ambient context leakage.
    """

    selected: dict[Any, dict[str, bool]] = defaultdict(dict)
    for tool in agent.mcp_tools.select_related("server"):
        if tool.enabled:
            selected[tool.server_id][tool.name] = bool(tool.requires_approval)

    toolsets: list[Any] = []
    for server in agent.mcp_servers.select_related("credential").order_by("name"):
        tools = selected.get(server.pk, {})
        if not tools:
            continue
        transport = _transport_for(server)
        toolset: Any = MCPToolset(transport, id=str(server.sqid))
        allowed = frozenset(tools)
        toolset = FilteredToolset(toolset, filter_func=_tool_filter(allowed))
        approvals = frozenset(name for name, required in tools.items() if required)
        if approvals:
            toolset = ApprovalRequiredToolset(
                toolset,
                approval_required_func=_approval_filter(approvals),
            )
        toolsets.append(toolset)
    return toolsets


def _transport_for(server: Any) -> Any:
    """Return the owner-native transport for one MCP server row."""

    url = str(server.resolved_url or "").strip()
    if not url:
        raise ValueError(f"MCP server {server.name!r} has no addressable URL.")
    headers: dict[str, str] = {}
    if server.builtin == "angee" and not server.credential_id:
        raise ValueError("The built-in Angee MCP server requires its agent bearer credential.")
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
