"""The agents addon's MCP bearer → actor verifier.

Each provisioned agent presents its own bearer to a platform-internal MCP server:
``MCPServer.bearer_for`` mints ``"<agent sqid>.<hmac>"`` from the server's
``agents.MCPServer.credential`` (an ``integrate.Credential``). This verifier parses that
shape, resolves the named agent, and confirms one of the agent's internal MCP servers
mints the presented digest — returning the agent subject the tool bodies run under. It is
named by ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``agents.autoconfig``); the base ``angee.mcp``
runtime calls it and has no knowledge of the catalogue.

This verifier authenticates the bearer to an agent subject only. Per-tool authorization
is enforced separately by ``agents.grants`` tool grants (the pydantic runtime's
``ToolGrantAccess``), so an authenticated agent still reaches only the tools it was granted.
"""

from __future__ import annotations

import logging

from django.apps import apps
from rebac import SubjectRef, system_context

from angee.agents.models import AgentLifecycle, MCPPlacement, RuntimeStatus

logger = logging.getLogger(__name__)


def resolve_actor(bearer: str) -> SubjectRef | None:
    """Return the MCP actor for ``bearer``, or ``None`` when it resolves to no agent.

    The bearer is ``"<agent sqid>.<hmac>"`` (see :meth:`MCPServer.bearer_for`): parse it,
    look the agent up by its public sqid, and — for a READY/RUNNING non-template agent —
    accept it when one of the agent's internal, credentialed MCP servers mints the presented
    digest (:meth:`MCPServer.accepts_bearer_digest`, a constant-time compare). Every decline
    returns ``None`` (no admin/user fallback) and is logged at WARNING with its reason and
    the sqid segment only — never the digest, bearer, or any secret — letting FastMCP deny
    the request.
    """

    if not bearer:
        logger.warning("MCP bearer declined: empty bearer")
        return None
    mcp_server = apps.get_model("agents", "MCPServer")
    agent_model = apps.get_model("agents", "Agent")
    parsed = mcp_server.parse_bearer(bearer)
    if parsed is None:
        logger.warning("MCP bearer declined: malformed bearer")
        return None
    sqid, digest = parsed
    with system_context(reason="agents.mcp.verify_bearer"):
        agent = agent_model._base_manager.filter(**agent_model.public_id_lookup(sqid)).first()
        if agent is None:
            logger.warning("MCP bearer declined: unknown agent %s", sqid)
            return None
        if (
            agent.is_template
            or agent.lifecycle != AgentLifecycle.READY
            or agent.runtime_status != RuntimeStatus.RUNNING
        ):
            logger.warning(
                "MCP bearer declined: agent %s is not ready/running (lifecycle=%s, runtime_status=%s)",
                sqid,
                agent.lifecycle,
                agent.runtime_status,
            )
            return None
        servers = (
            agent.mcp_servers.filter(placement=MCPPlacement.INTERNAL)
            .exclude(credential__isnull=True)
            .select_related("credential")
        )
        for server in servers:
            if server.accepts_bearer_digest(agent, digest):
                return agent.principal_subject()
        logger.warning("MCP bearer declined: no internal MCP server of agent %s accepts this bearer", sqid)
        return None
