"""The process-wide MCP server: one FastMCP instance, tools from addon manifests.

Each installed addon contributes tools by declaring an ``mcp_tools`` manifest
attribute on its ``AppConfig`` — a ``"<module>.<attr>"`` dotted reference to a
``register(server: FastMCP) -> None`` callable (the same declaration shape the
GraphQL ``schemas`` seam uses, resolved by the shared
:func:`angee.addons.resolve_addon_reference`). The server authenticates the inbound
bearer with :class:`~angee.mcp.verifier.RebacTokenVerifier` and brackets every tool
call in the authenticated REBAC actor with :class:`~angee.mcp.middleware.ActorMiddleware`.
It is mounted as a StreamableHTTP ASGI app by :mod:`angee.mcp.asgi`; :mod:`angee.asgi`
owns its lifespan, so the server holds no per-request lifecycle of its own.

``stateless_http`` keeps each call independent and ``json_response`` returns a
buffered JSON body the agent's HTTP client folds without an SSE reader (both passed
to :meth:`~fastmcp.FastMCP.http_app`). DNS-rebinding protection is off by default
(FastMCP passes no transport-security settings); Django's ``ALLOWED_HOSTS`` already
terminates the request and the bearer + REBAC actor resolution is the real
authorization boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from fastmcp import FastMCP

from angee.addons import is_angee_addon, resolve_addon_reference
from angee.mcp.middleware import ActorMiddleware
from angee.mcp.verifier import RebacTokenVerifier

if TYPE_CHECKING:  # pragma: no cover
    from starlette.applications import Starlette

ToolRegistrar = Callable[[FastMCP], None]
"""An addon's ``register(server)`` callable — it adds tools to the MCP server."""

MOUNT_PATH = "/mcp"
"""The external StreamableHTTP path the server mounts at (see :mod:`angee.mcp.asgi`)."""


@cache
def mcp_server() -> FastMCP:
    """Return the process-wide FastMCP server, built and tool-registered once."""

    server = FastMCP(name="angee", auth=RebacTokenVerifier(), middleware=[ActorMiddleware()])
    for registrar in _registrars():
        registrar(server)
    return server


@cache
def mcp_app() -> Starlette:
    """Return the server's StreamableHTTP ASGI app (built once, lifespan owned by the entrypoint)."""

    return mcp_server().http_app(path=MOUNT_PATH, stateless_http=True, json_response=True)


def has_tools() -> bool:
    """Return whether any installed addon contributes MCP tools."""

    return bool(_registrars())


@cache
def _registrars() -> tuple[ToolRegistrar, ...]:
    """Return every ``register`` callable declared by an installed addon's ``mcp_tools``.

    Iterates the app registry in install order, so the registration set is
    deterministic.
    """

    registrars: list[ToolRegistrar] = []
    for app_config in apps.get_app_configs():
        if not is_angee_addon(app_config):
            continue
        declaration = getattr(app_config, "mcp_tools", None)
        if declaration is None:
            continue
        if not isinstance(declaration, str):
            raise ImproperlyConfigured(f"{app_config.name}.mcp_tools must be a dotted reference")
        registrar = resolve_addon_reference(app_config, declaration, attr="mcp_tools")
        if not callable(registrar):
            raise ImproperlyConfigured(f"{app_config.name}.mcp_tools must reference a callable")
        registrars.append(registrar)
    return tuple(registrars)
