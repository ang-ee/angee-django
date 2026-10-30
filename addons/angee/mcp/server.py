"""The process-wide MCP server: one FastMCP instance, tools from addon packages.

Enabled addons contribute a conventional ``mcp_tools.register(server)`` callable.
The server authenticates inbound bearers through RebacTokenVerifier and carries
the actor through ActorMiddleware. MCPConfig supplies the HTTP mount and native
FastMCP lifespan to django-compose's ASGI contribution owner. The inner HTTP
application serves its root; the native mount owns the external ``/mcp`` prefix.

``stateless_http`` keeps each call independent and ``json_response`` returns a
buffered JSON body the agent's HTTP client folds without an SSE reader (both passed
to :meth:`~fastmcp.FastMCP.http_app`). FastMCP's native host/origin guard protects this ASGI mount directly;
Django request middleware does not run on mounted MCP requests. Django allowed
host patterns and trusted browser origins supply deployment intent, translated
to the native guard's glob semantics. The guard also admits its native local
host defaults. Bearer + REBAC actor resolution remains the authorization boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from typing import TYPE_CHECKING

from django.apps import AppConfig, apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django_compose.components import InstalledComponents, installed_components
from fastmcp import FastMCP

from angee.base.addons import optional_addon_module
from angee.mcp.middleware import ActorMiddleware
from angee.mcp.verifier import RebacTokenVerifier

if TYPE_CHECKING:  # pragma: no cover
    from fastmcp.server.http import StarletteWithLifespan

ToolRegistrar = Callable[[FastMCP], None]
"""An addon's ``register(server)`` callable — it adds tools to the MCP server.

Non-GraphQL tools must explicitly declare ``ToolAnnotations.readOnlyHint``.
Compiled GraphQL tools carry their root ``op_type`` as the structural actor-posture
fact (their annotation must agree). Builtins also run through the in-process
pydantic runtime, so their callable may not depend on FastMCP's request-scoped
``Context`` or ``get_access_token``; the native toolset asserts these contracts
when it advertises a granted tool.
"""

MOUNT_PATH = "/mcp"
"""The external StreamableHTTP path contributed by MCPConfig.compose_asgi."""


@cache
def mcp_server() -> FastMCP:
    """Return the process-wide FastMCP server, built and tool-registered once."""

    server = FastMCP(
        name="angee",
        auth=RebacTokenVerifier(),
        middleware=[ActorMiddleware()],
        mask_error_details=True,
    )
    for registrar in _registrars():
        registrar(server)
    return server


@cache
def mcp_app() -> StarletteWithLifespan:
    """Return the server's StreamableHTTP ASGI app (built once, lifespan owned by the entrypoint)."""

    # Django's leading-dot suffix syntax includes the apex; native FastMCP uses
    # globs. Leave header normalization and request/origin comparison upstream.
    hosts = [
        pattern
        for host in settings.ALLOWED_HOSTS
        for pattern in ((host[1:], f"*{host}") if host.startswith(".") else (host,))
    ]
    return mcp_server().http_app(
        path="/", stateless_http=True, json_response=True,
        host_origin_protection=True, allowed_hosts=hosts,
        allowed_origins=list(settings.CSRF_TRUSTED_ORIGINS),
    )


def has_tools() -> bool:
    """Return whether any installed addon contributes MCP tools."""

    return bool(_registrars())


@cache
def _registrars() -> tuple[ToolRegistrar, ...]:
    """Return every ``register`` callable declared by an installed addon's ``mcp_tools``.

    Iterates the app registry in install order, so the registration set is
    deterministic.
    """

    index = installed_components("angee.addons")
    return tuple(
        registrar for config in apps.get_app_configs()
        if (registrar := tool_registrar(config, index=index)) is not None
    )


def tool_registrar(app_config: AppConfig, *, index: InstalledComponents | None = None) -> ToolRegistrar | None:
    """Load the conventional registrar only from an enabled addon."""

    module = optional_addon_module(app_config, 'mcp_tools', index=index)
    if module is None or not hasattr(module, 'register'):
        return None
    registrar = module.register
    if not callable(registrar):
        raise ImproperlyConfigured(f'{app_config.name}.mcp_tools.register must be callable')
    return registrar
