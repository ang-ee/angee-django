"""MCP server — the generic seam that mounts one FastMCP server for the process.

``angee.mcp.asgi`` contributes the StreamableHTTP mount at ``/mcp`` and its native
lifespan to ``angee.server.asgi``. A FastMCP token verifier resolves the bearer to
a REBAC actor through ``ANGEE_MCP_ACTOR_VERIFIER``, which the catalogue owner
supplies, and every tool runs under that actor. The server generates a bounded,
read-only catalogue from the console GraphQL resource registry; other addons
contribute curated tools through a conventional ``mcp_tools.py`` with a
``register(server)`` callable. Built-in tools declare actor-only execution in
native ``Tool.meta`` so the in-process agent runtime can admit them.
"""
