"""The process-wide MCP server: one FastMCP instance, tools from addon manifests.

Each installed addon contributes tools through the ``mcp_tools`` seam — an
``mcp_tools.py`` module exposing a ``register(server: FastMCP) -> None`` callable,
inferred by convention (an ``addon.toml`` ``[mcp].tools`` entry overrides the dotted
reference). It resolves the same way the GraphQL ``schemas`` seam does, through the
shared :func:`angee.addons.resolve_addon_reference`. The server authenticates the inbound
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

from angee.addons import addon_contract, is_angee_addon, resolve_addon_reference
from angee.mcp.middleware import ActorMiddleware
from angee.mcp.verifier import RebacTokenVerifier

if TYPE_CHECKING:  # pragma: no cover
    from starlette.applications import Starlette

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
"""The external StreamableHTTP path the server mounts at (see :mod:`angee.mcp.asgi`)."""


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
        contract = addon_contract(app_config)
        declaration = contract.mcp_tools if contract is not None else None
        if declaration is None:
            continue
        registrar = resolve_addon_reference(app_config, declaration, attr="mcp_tools")
        if not callable(registrar):
            raise ImproperlyConfigured(f"{app_config.name}.mcp_tools must reference a callable")
        registrars.append(registrar)
    return tuple(registrars)
