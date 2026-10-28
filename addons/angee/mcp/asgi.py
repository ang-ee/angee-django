"""MCP owns its HTTP mount and explicit native FastMCP lifespan contribution.

Contributes neither when no installed addon declares MCP tools. The host enters
FastMCP's public ``app.lifespan(app)`` context once per serving process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import Any

from angee.mcp.server import MOUNT_PATH, has_tools, mcp_app

ASGIApp = Callable[
    [MutableMapping[str, Any], Callable[[], Awaitable[Any]], Callable[[Any], Awaitable[None]]],
    Awaitable[None],
]


def http_mounts() -> list[tuple[str, ASGIApp]]:
    """Return the ``(path_prefix, ASGI app)`` mounts this addon contributes."""

    if not has_tools():
        return []
    return [(MOUNT_PATH, mcp_app())]


def lifespans() -> list[Callable[[], AbstractAsyncContextManager[Any]]]:
    """Declare the native StreamableHTTP session-manager lifecycle explicitly."""

    if not has_tools():
        return []
    app = mcp_app()
    return [partial(app.lifespan, app)]
