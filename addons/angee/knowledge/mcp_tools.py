"""Knowledge tools for the MCP server — curated GraphQL operations over the public bucket.

Each tool runs the same actor-scoped GraphQL operation a browser would, so the
schema's ``permission_classes`` and ``RebacManager`` scoping do the authorization — no
hand-rolled ORM access or projection. The :mod:`angee.mcp.graphql` engine derives each
tool's input schema, response projection, and operation document from the schema; this
module only declares which knowledge operations to expose and how to project them.

ids are the public ``sqid``; agent field and argument names are snake_case (the compiler
maps them to the schema's wire names). Reads are outline- and backlink-aware via the
nested projection grammar; the body writes return the shared :class:`PageBodyPayload`
(``ok``/``error``/``error_code`` + the fresh ``body_hash`` for the next CAS edit).
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools

# Backlinks resolve to the linking page's sqid, title, and the link's display text.
_BACKLINKS = ("backlinks", ("page", "title", "display_text"))

# Outline-aware body read: the markdown sidecar (nullable) with its heading outline.
_MARKDOWN = ("markdown", ("body", "body_hash", "word_count", ("outline", ("level", "text", "slug"))))

# Every body write returns the shared payload; project the fresh hash for the next edit.
_BODY_RESULT = ("ok", "error", "error_code", ("markdown", ("body_hash",)))


def register(server: FastMCP) -> None:
    """Register the knowledge tools (GraphQL-backed) on the MCP server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="pages_by_pk",
                name="read_page",
                fields=("sqid", "title", "kind", _MARKDOWN, _BACKLINKS),
                id_arg="id",
                description="Read one page by its public id (sqid): title, kind, the markdown body with its "
                "heading outline, and the pages that link to it.",
            ),
            GraphQLTool(
                operation="search_pages",
                name="search_pages",
                fields=("sqid", "title", "kind"),
                args=("vault", "query", "first"),
                description="Search a vault for pages matching a query, returning the matching pages. "
                "vault is the vault's public id (sqid).",
            ),
            GraphQLTool(
                operation="patch_page_section",
                name="patch_page_section",
                fields=_BODY_RESULT,
                args=("page", "heading_path", "op", "content", "expected_hash"),
                description="Replace, append to, or prepend to the section at heading_path (the heading's "
                "ancestor titles) in a page body. op is REPLACE/APPEND/PREPEND; pass expected_hash from a "
                "prior read to guard against a stale overwrite.",
            ),
            GraphQLTool(
                operation="replace_page_text",
                name="replace_page_text",
                fields=_BODY_RESULT,
                args=("page", "old", "new", "expected_hash"),
                description="Replace the single occurrence of an exact string in a page body. Fails if old is "
                "absent or appears more than once; pass expected_hash to guard against a stale overwrite.",
            ),
            GraphQLTool(
                operation="append_to_page",
                name="append_to_page",
                fields=_BODY_RESULT,
                args=("page", "content", "expected_hash"),
                description="Append content to the end of a page body, one blank line after the existing text. "
                "Pass expected_hash from a prior read to guard against a stale overwrite.",
            ),
            GraphQLTool(
                operation="pages_by_pk",
                name="page_backlinks",
                fields=("sqid", "title", _BACKLINKS),
                id_arg="id",
                description="List the pages that link to a page, by its public id (sqid).",
            ),
        ],
    )
