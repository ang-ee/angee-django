"""Django config for the MCP server base addon."""

from __future__ import annotations

from django.apps import AppConfig


class McpConfig(AppConfig):
    """Source app manifest for the MCP server.

    Owns the generic seam only: it mounts one FastMCP StreamableHTTP ASGI app at
    ``/mcp`` (the ``http_mounts`` seam in :mod:`angee.mcp.asgi`), authenticates the
    inbound bearer to a REBAC actor, and runs each tool body under that actor.
    Addons contribute their tools by declaring an ``mcp_tools`` manifest attribute
    on their own ``AppConfig`` (a ``"<module>.<attr>"`` dotted reference to a
    ``register(server)`` callable); the credential→actor mapping is supplied by
    whichever addon owns the MCP catalogue, through the ``ANGEE_MCP_ACTOR_VERIFIER``
    setting. This addon imports neither.
    """

    default = True
    angee_addon = True
    default_auto_field = "django.db.models.BigAutoField"
    name = "angee.mcp"
    label = "mcp"
    depends_on = ("angee.iam",)
