"""ASGI mount contribution: the StreamableHTTP app at ``/mcp``.

:mod:`angee.asgi` discovers this addon's ``http_mounts``, mounts the app at
``/mcp``, and enters its StreamableHTTP lifespan (the ``http_app``'s
``router.lifespan_context``, which owns FastMCP v2's session manager) once for the
process from the server's ASGI lifespan. Contributes nothing when no installed addon
declares ``mcp_tools``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
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
