"""MCP mounts one native FastMCP StreamableHTTP server at ``/mcp``.

MCPConfig contributes its mount and lifespan through django-compose. The bearer
verifier resolves the REBAC actor; generated resource readers and conventional
``mcp_tools.register(server)`` contributions reuse the canonical GraphQL gates.
Actor-only in-process tools declare that execution context in native Tool.meta.
"""

RESOURCE_READER_TOOL_TAG = "angee:resource_reader"
"""Native tool tag for generated resource-reader capabilities."""
