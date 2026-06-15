"""Settings fragments required by the MCP server addon."""

from __future__ import annotations

# The base MCP addon contributes NO ``ANGEE_MCP_ACTOR_VERIFIER`` default on purpose: the
# catalogue owner (e.g. the agents addon) supplies it, and a base-level ``""`` default would
# clobber that value in autoconfig merge order — leaving every bearer unauthenticated (401).
# Absent any verifier, ``angee.mcp.verifier`` reads ``getattr(settings,
# "ANGEE_MCP_ACTOR_VERIFIER", "")`` → no verifier → fail-closed. The authenticated actor is
# then bracketed around each tool call by ``angee.mcp.middleware.ActorMiddleware`` and read
# through rebac's ambient ``current_actor`` (no ``REBAC_MCP_ACTOR_RESOLVER`` override needed).
SETTINGS: dict[str, str] = {}
"""Django settings contributed when the MCP server addon is installed."""
