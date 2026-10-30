"""MCP owns its HTTP mount and explicit native FastMCP lifespan contribution.

Contributes neither when no installed addon declares MCP tools. The host enters
FastMCP's public ``app.lifespan(app)`` context once per serving process.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from functools import partial
from typing import Any, cast

from asgiref.typing import ASGI3Application

from angee.mcp.server import MOUNT_PATH, has_tools, mcp_app


def http_mounts() -> list[tuple[str, ASGI3Application]]:
    """Return the ``(path_prefix, ASGI app)`` mounts this addon contributes."""

    if not has_tools():
        return []
    # Starlette types ASGI messages as MutableMapping; its native application
    # implements the ASGI 3 protocol represented by asgiref's TypedDicts.
    return [(MOUNT_PATH, cast(ASGI3Application, mcp_app()))]


def lifespans() -> list[Callable[[], AbstractAsyncContextManager[Any]]]:
    """Declare the native StreamableHTTP session-manager lifecycle explicitly."""

    if not has_tools():
        return []
    app = mcp_app()
    return [partial(app.lifespan, app)]
