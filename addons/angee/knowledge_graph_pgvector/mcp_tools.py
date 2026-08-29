"""MCP tools contributed by the knowledge pgvector plugin.

Adds one ``semantic_search`` tool over the ``semantic_search`` query this plugin
contributes in :mod:`~angee.knowledge_graph_pgvector.schema`. It reuses the same
actor-scoped GraphQL engine the knowledge tools use — the compiler derives the
input schema, projection, and document from the schema, so this module only names
the operation and how to project it. Registered through the ``mcp_tools`` AppConfig
seam with **no edit to the knowledge addon**; ``mcp_tools`` (never ``mcp``) keeps
the module from shadowing the third-party ``mcp`` package.
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools


def register(server: FastMCP) -> None:
    """Register the pgvector plugin's MCP tools on the server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="semantic_search",
                name="semantic_search",
                fields=("sqid", "title", "kind"),
                args=("vault", "query", "first"),
                description="Semantically search a vault for pages related to a query, most relevant first. "
                "vault is the vault's public id (sqid). Uses the plugin's vector retrieval provider.",
            ),
        ],
    )
