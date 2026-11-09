"""Related-page MCP reads use the same actor-scoped GraphQL operation."""

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools


def register(server: FastMCP) -> None:
    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="related_pages",
                name="related_pages",
                fields=("sqid", "title", "kind"),
                args=("page",),
                limit_arg="first",
                description="Find readable pages related by passage embeddings and one-hop wikilinks. "
                "page is a public page id in a vault selecting pgvector.",
            ),
        ],
    )
