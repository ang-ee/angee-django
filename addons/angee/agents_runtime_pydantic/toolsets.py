"""Project an agent's selected MCP rows into pydantic-ai toolsets."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastmcp.client.transports import FastMCPTransport, StreamableHttpTransport
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.toolsets import ApprovalRequiredToolset, FilteredToolset

from angee.mcp.server import mcp_server


def toolsets_for_agent(agent: Any) -> list[Any]:
    """Return MCP toolsets narrowed to the tools explicitly selected by ``agent``.

    The built-in Angee server uses FastMCP's in-memory transport. The caller's
    ambient agent actor crosses the async bridge as a context variable, so the
    server's actor middleware stays fail-closed without an HTTP bearer round trip.
    External servers retain their Streamable HTTP transport and live credential.
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
        toolset = FilteredToolset(toolset, filter_func=lambda _ctx, definition, names=allowed: definition.name in names)
        approvals = frozenset(name for name, required in tools.items() if required)
        if approvals:
            toolset = ApprovalRequiredToolset(
                toolset,
                approval_required_func=lambda _ctx, definition, _args, names=approvals: definition.name in names,
            )
        toolsets.append(toolset)
    return toolsets


def _transport_for(server: Any) -> Any:
    """Return the owner-native transport for one MCP server row."""

    if server.builtin == "angee":
        return FastMCPTransport(mcp_server())
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
