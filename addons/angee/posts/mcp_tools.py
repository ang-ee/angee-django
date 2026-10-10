"""Bounded comment context through the actor-scoped GraphQL tool seam."""

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools


def register(server: FastMCP) -> None:
    """Expose public message ids, readable parents, and bounded plain-text bodies."""

    register_graphql_tools(server, [
        GraphQLTool(
            name="read_comment_thread", schema="console", operation="messages_by_pk",
            id_arg="id",
            fields=(
                "sqid", "preview", "direction", "sender_name", "sent_at", "is_original_post",
                ("parent", ("sqid", "preview", "sender_name", "sent_at")),
                ("thread", ("sqid", "subject_url", ("title", ("text",)))),
            ),
            description="Read a comment's author, send time, preview, immediate parent and public thread URL "
            "by sqid. Use read_message_text for its body.",
        ),
        GraphQLTool(
            name="read_message_text", schema="console", operation="messages_by_pk",
            id_arg="id", fields=("body_text",),
            description="Read up to 4 KiB of an accessible message's plain-text body by sqid.",
        ),
    ])
