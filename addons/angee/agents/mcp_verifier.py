"""The agents addon's MCP bearer → actor verifier.

An ``agents.MCPServer.credential`` (an ``integrate.Credential``) holds the bearer the
agent presents to an internal MCP server. This verifier matches an inbound bearer
to that credential and resolves it to the actor the tool bodies run under. It is
named by ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``agents.autoconfig``); the base
``angee.mcp`` runtime calls it and has no knowledge of the catalogue.
"""

from __future__ import annotations

import hmac
from typing import Any

from django.apps import apps
from rebac import SubjectRef, system_context, to_subject_ref


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
        for server in mcp_server.objects.exclude(credential__isnull=True).select_related(
            "credential", "credential__user"
        ):
            if hmac.compare_digest(str(server.credential.secret_value()), bearer):
                return _run_as_user(server)
    return None


def _run_as_user(server: Any) -> SubjectRef | None:
    """Return the user the agent runs as: the owner of the server's credential.

    Interim model (option A): the agent acts with the identity of the user who owns the
    credential it presents, so it gets that user's full notes CRUD (read / create /
    update / delete) with correct attribution — ``created_by`` / ``owner`` are user FKs
    and the row scoping is user-relation based. ``None`` (a credential with no user)
    denies, since there is no one to attribute the call to.

    TODO(agent-identity, option B — deferred): a distinct ``agents/agent`` subject that
    is *granted* a role / user / resource for authz while attributing writes to a
    designated user — preserving a distinct agent identity through CRUD. It needs the
    resource schemas to accept ``agents/agent`` subjects, an agent-aware ``created_by``
    author, and per-agent grants. Until then the agent borrows its credential owner's
    identity.
    TODO(mcp-authz): no per-tool authz yet — any holder of the server's credential
    reaches every registered tool.
    """

    user = getattr(server.credential, "user", None)
    return to_subject_ref(user) if user is not None else None
