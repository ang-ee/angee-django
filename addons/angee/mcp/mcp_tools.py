"""Register the MCP addon's generated data-resource read tools."""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.resource_tools import register_resource_tools


def register(server: FastMCP) -> None:
    """Register generic readers compiled from the console resource registry."""

    register_resource_tools(server)
