"""Read the MCP request actor back off FastMCP's authenticated token.

The transport authenticates the bearer (:mod:`angee.mcp.verifier`) and FastMCP
stashes the resolved actor on the request; this reads it back as a
:class:`~rebac.SubjectRef`. Two readers of the one fact:

- :func:`actor_from_request` — the optional form, shaped to rebac's
  ``REBAC_MCP_ACTOR_RESOLVER`` signature so ``rebac_mcp_tool`` resolves the same
  actor; returns ``None`` when the request carried no authenticated token.
- :func:`request_actor` — the strict form for a tool body that scopes its own
  queryset (no single target resource, so the decorator doesn't apply); it binds
  the actor with ``rebac.actor_context`` and raises rather than read unscoped.
"""

from __future__ import annotations

from typing import Any

from rebac import SubjectRef

from mcp.server.auth.middleware.auth_context import get_access_token


def actor_from_request(ctx: Any = None) -> SubjectRef | None:
    """Return the actor FastMCP authenticated for this request, or ``None``.

    ``ctx`` is accepted (and ignored) so this satisfies rebac's
    ``REBAC_MCP_ACTOR_RESOLVER`` signature; the actor rides FastMCP's auth
    context, not the MCP request metadata. The verifier stored it as the token's
    canonical ``subject`` string (:class:`~angee.mcp.verifier.RebacTokenVerifier`).
    """

    token = get_access_token()
    if token is None or not token.subject:
        return None
    return SubjectRef.parse(token.subject)


def request_actor() -> SubjectRef:
    """Return the authenticated MCP actor, or raise to deny.

    The strict counterpart to :func:`actor_from_request`: ``None`` means the bearer
    matched no MCP credential, so a queryset-scoping tool body denies rather than
    read unscoped.
    """

    actor = actor_from_request()
    if not isinstance(actor, SubjectRef):
        raise PermissionError("MCP request is not authenticated.")
    return actor
