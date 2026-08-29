"""Per-call MCP middleware: bracket each tool body in the authenticated REBAC actor.

The MCP analog of rebac's ``ActorMiddleware``. The transport authenticates the bearer
(:class:`~angee.mcp.verifier.RebacTokenVerifier`) and FastMCP stashes the resolved
actor on the request; this reads it back off
:func:`~fastmcp.server.dependencies.get_access_token` and enters
``rebac.actor_context`` for the duration of the tool call. Tool bodies — and any
GraphQL execution they drive — then scope to that actor through rebac's ambient
:func:`rebac.current_actor`; no per-tool actor plumbing. A call that resolved to no
actor runs actor-less, and rebac denies it (fail-closed).
"""

from __future__ import annotations

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from rebac import SubjectRef, actor_context


class ActorMiddleware(Middleware):
    """Enter the authenticated REBAC actor's context around every tool call."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Run the tool body under ``actor_context`` so its querysets scope to the actor."""

        actor = _request_actor()
        if actor is None:
            return await call_next(context)
        with actor_context(actor):
            return await call_next(context)


def _request_actor() -> SubjectRef | None:
    """Return the actor FastMCP authenticated for this request, or ``None``.

    The verifier stored the canonical subject string on the token's ``subject``; this
    parses it back into a :class:`~rebac.SubjectRef`. ``None`` when the request carried
    no authenticated token (then the body runs actor-less and rebac denies it).
    """

    token = get_access_token()
    subject = getattr(token, "subject", None) if token is not None else None
    return SubjectRef.parse(subject) if subject else None
