"""The agents addon's MCP bearer → actor verifier.

An ``agents.MCPServer.credential`` (an ``integrate.Credential``) holds the bearer the
agent presents to an internal MCP server. This verifier matches an inbound bearer
to that credential and resolves it to the agent actor the tool bodies run under. It is
named by ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``agents.autoconfig``); the base
``angee.mcp`` runtime calls it and has no knowledge of the catalogue.

Caveat: this verifier authenticates the bearer to an agent subject only. Per-tool
authorization is not yet enforced at this layer: any authenticated agent reaches
every registered tool body, gated only by the tool's underlying REBAC scoping.
Shared-server credentials resolving to more than one candidate agent fail closed
(``None``). Per-agent bearers plus per-tool gating are deferred to
``.work/plans/fork-a6-mcp-authz-deferred.md``.
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
    digest so the match leaks no timing. A bearer must resolve to exactly one
    provisioned non-template agent; no match, no agent, or multiple agents returns
    ``None`` (no admin/user fallback) and lets FastMCP deny the request.
    """

    if not bearer:
        return None
    mcp_server = apps.get_model("agents", "MCPServer")
    agents: dict[Any, Any] = {}
    with system_context(reason="agents.mcp.verify_bearer"):
        for server in mcp_server.objects.exclude(credential__isnull=True).select_related(
            "credential"
        ).prefetch_related("agents"):
            if hmac.compare_digest(str(server.credential.secret_value()), bearer):
                for agent in server.agents.all():
                    if agent.is_template or str(agent.lifecycle) != "ready" or not (agent.workspace or agent.service):
                        continue
                    agents[agent.pk] = agent
        if len(agents) != 1:
            return None
        return next(iter(agents.values())).principal_subject()
