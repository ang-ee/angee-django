"""Notes tools for the MCP server — curated GraphQL operations over the public bucket.

Each tool runs the same actor-scoped GraphQL operation a browser would (notes/note CRUD
on the ``public`` schema bucket), so strawberry's ``RebacManager`` scoping and the crud
create/write gates do the authorization — no hand-rolled ORM access or projection. The
:mod:`angee.mcp.graphql` engine derives each tool's input schema, response projection,
and operation document from the schema; this module only declares which operations to
expose and how to project them. ids are the public ``sqid``; fields are snake_case.
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools

# ``is_starred`` / ``reminder_at`` are owner-gated (``read__is_starred = owner``): an agent
# acting as a non-owner (e.g. a platform admin editing another user's note) gets them
# redacted to null, which the non-nullable GraphQL fields reject. Project only ungated fields.
_SUMMARY = ("sqid", "title", "status", "word_count")
_DETAIL = (*_SUMMARY, "body", "tags")


def register(server: FastMCP) -> None:
    """Register the notes tools (GraphQL-backed) on the MCP server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="notes",
                name="list_notes",
                fields=_SUMMARY,
                limit_arg="limit",
                description="List the caller's notes, most-recently-updated first.",
            ),
            GraphQLTool(
                operation="note",
                name="read_note",
                fields=_DETAIL,
                id_arg="id",
                description="Return one note in full by its public id (sqid).",
            ),
            GraphQLTool(
                operation="createNote",
                name="create_note",
                fields=_DETAIL,
                flatten="data",
                description="Create a note owned by the caller and return it.",
            ),
            GraphQLTool(
                operation="updateNote",
                name="update_note",
                fields=_DETAIL,
                flatten="data",
                description="Update fields of a note the caller may write, and return it.",
            ),
            GraphQLTool(
                operation="deleteNote",
                name="delete_note",
                fields=("total_deleted_count",),
                id_arg="id",
                fixed={"confirm": True},
                description="Delete a note the caller may delete, by its public id (sqid).",
            ),
        ],
    )
