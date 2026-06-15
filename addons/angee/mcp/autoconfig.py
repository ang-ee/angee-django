"""Settings fragments required by the MCP server addon."""

from __future__ import annotations

SETTINGS = {
    # The catalogue owner supplies the bearer→actor verifier under
    # ``ANGEE_MCP_ACTOR_VERIFIER`` (see ``angee.mcp.verifier``); absent one, an MCP
    # bearer resolves to no actor and FastMCP denies the request (401). The
    # authenticated actor is then bracketed around each tool call by
    # ``angee.mcp.middleware.ActorMiddleware`` and read through rebac's ambient
    # ``current_actor`` — so no ``REBAC_MCP_ACTOR_RESOLVER`` override is needed.
    "ANGEE_MCP_ACTOR_VERIFIER": "",
}
"""Django settings contributed when the MCP server addon is installed."""
