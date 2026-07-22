"""MCP server — the generic seam that mounts one FastMCP server for the process.

Mounts a single StreamableHTTP ASGI app at ``/mcp`` (via the ``http_mounts``
seam in :mod:`angee.mcp.asgi`, which :mod:`angee.asgi` discovers and gives a
lifespan), authenticates the bearer to a REBAC actor with a FastMCP token
verifier, and authorizes each tool with rebac. It generates a bounded, read-only
catalogue from the console GraphQL resource registry; other addons contribute
curated tools by adding an ``mcp_tools.py`` with a ``register(server)`` callable
(the conventional ``mcp_tools`` seam, inferred from the file; an ``addon.toml``
``[mcp].tools`` entry overrides the dotted reference). The catalogue owner supplies
the bearer→actor verifier through ``ANGEE_MCP_ACTOR_VERIFIER``.
"""
