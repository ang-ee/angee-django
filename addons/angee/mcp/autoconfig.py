"""Settings fragments required by the MCP server addon."""

from __future__ import annotations

SETTINGS = {
    # The catalogue owner supplies the bearer→actor verifier under
    # ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``angee.mcp.verifier``); absent one, an MCP
    # bearer resolves to no actor and FastMCP denies the request (401).
    "ANGEE_MCP_ACTOR_VERIFIER": "",
    # rebac's ``rebac_mcp_tool`` reads the authenticated actor through this resolver
    # (the actor rides FastMCP's auth context, not the request metadata default).
    "REBAC_MCP_ACTOR_RESOLVER": "angee.mcp.actors.actor_from_request",
    # OAuth issuer advertised in the StreamableHTTP protected-resource metadata.
    # Cosmetic for the bearer-credential model, but FastMCP requires it to enable
    # the token verifier; override per deployment with the public base URL.
    "ANGEE_MCP_ISSUER_URL": "http://localhost",
}
"""Django settings contributed when the MCP server addon is installed."""
