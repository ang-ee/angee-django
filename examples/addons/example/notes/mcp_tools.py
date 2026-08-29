"""Notes tools for the MCP server — curated GraphQL operations over the public bucket.

Each tool runs the same actor-scoped GraphQL operation a browser would (the
Hasura-shaped notes resource on the ``public`` schema bucket), so strawberry's
``RebacManager`` scoping and the Angee write backend do the authorization. The
create tool explicitly requires a user actor before GraphQL execution because the
product policy still makes note ownership a human-user action. Agent service
accounts now make relaxing that gate possible as a future product decision. The
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
                operation="notes_by_pk",
                name="read_note",
                fields=_DETAIL,
                id_arg="id",
                description="Return one note in full by its public id (sqid).",
            ),
            GraphQLTool(
                operation="insert_notes_one",
                name="create_note",
                fields=_DETAIL,
                flatten="object",
                requires_user_actor=True,
                description="Create a note owned by the user caller and return it.",
            ),
            GraphQLTool(
                operation="update_notes_by_pk",
                name="update_note",
                fields=_DETAIL,
                id_arg="pk_columns",
                flatten="_set",
                description="Update fields of a note the caller may write, and return it.",
            ),
            GraphQLTool(
                operation="delete_note",
                name="delete_note",
                fields=("total_deleted_count",),
                id_arg="id",
                fixed={"confirm": True},
                description="Delete a note the caller may delete, by its public id (sqid).",
            ),
        ],
    )
