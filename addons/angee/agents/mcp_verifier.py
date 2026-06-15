"""The agents addon's MCP bearer → actor verifier.

An ``agents.MCPServer.credential`` (an ``iam.Credential``) holds the bearer the
agent presents to an internal MCP server. This verifier matches an inbound bearer
to that credential and resolves it to the actor the tool bodies run under. It is
named by ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``agents.autoconfig``); the base
``angee.mcp`` runtime calls it and has no knowledge of the catalogue.
"""

from __future__ import annotations

import hmac
from typing import Any

from django.apps import apps
from rebac import SubjectRef, system_context


def resolve_actor(bearer: str) -> SubjectRef | None:
    """Return the MCP actor for ``bearer``, or ``None`` when no credential matches.

    Credential material is encrypted at rest, so the bearer can't be queried by
    column: the candidate set is the (small, bounded) credentials backing MCP
    servers, compared by their decrypted ``secret_value()`` with a constant-time
    digest so the match leaks no timing. ``None`` (no admin fallback) lets FastMCP
    deny the request.
    """

    if not bearer:
        return None
    mcp_server = apps.get_model("agents", "MCPServer")
    with system_context(reason="agents.mcp.verify_bearer"):
        for server in mcp_server.objects.exclude(credential__isnull=True).select_related("credential"):
            if hmac.compare_digest(str(server.credential.secret_value()), bearer):
                return _agent_actor(server)
    return None


def _agent_actor(server: Any) -> SubjectRef:
    """Return the agent-scoped actor an accepted MCP bearer resolves to.

    A real, non-admin ``agents/agent`` subject — scoped per server credential so
    distinct servers map to distinct actors, and reaching only the rows that subject
    is granted (never the owning user's full scope, never elevated).
    """

    # TODO(agent-actor): the bearer must identify the *agent* actor, not the owning
    # user. Full agent-actor identity — an ``agents/agent`` subject minted per
    # provisioned agent, with the REBAC reach the agent is granted over the resources
    # it may touch — is deferred. For now the credential's own sqid is the placeholder
    # agent id, so the actor is a stable, distinct, non-user subject the tool reads
    # scope to (it sees only what it is granted, not the owner's notes).
    # TODO(mcp-authz): the resolver/authz must also check that the *agent* selecting
    # this server is permitted to use it and which of its tools — deferred to the next
    # slice. Today any holder of the server's credential reaches every registered tool.
    return SubjectRef.of("agents/agent", str(server.credential.sqid))
