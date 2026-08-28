"""Parties tools for the MCP server — curated actor-scoped GraphQL operations.

The tools run the same public GraphQL fields as the browser and use the
:class:`~angee.mcp.graphql.GraphQLTool` compiler for input schemas, documents,
projection, and permission enforcement. Agent-facing names are snake_case and
every id is an opaque public sqid (prefixes are called out per tool).
"""

from __future__ import annotations

from fastmcp import FastMCP

from angee.mcp.graphql import GraphQLTool, register_graphql_tools

_PARTY_SUMMARY = ("sqid", "display_name")
_HANDLE_SUMMARY = ("sqid", "platform", "value", "label", "is_preferred")
_CIRCLE_MEMBERSHIP = (
    "sqid",
    ("circle", ("sqid", "name")),
    "confidence",
    "source",
)
_RELATIONSHIP = (
    "sqid",
    ("kind", ("sqid", "name", "inverse_name")),
    ("other_party", _PARTY_SUMMARY),
    "other_name",
    "title",
    "started_at",
    "ended_at",
    "notes",
)
_IDENTITY_DECISION = (
    "sqid",
    ("party", _PARTY_SUMMARY),
    ("handle", ("sqid", "platform", "value")),
    "confidence",
    "source",
    "is_confirmed",
    "is_dismissed",
)


def register(server: FastMCP) -> None:
    """Register the GraphQL-backed parties tools on the MCP server."""

    register_graphql_tools(
        server,
        [
            GraphQLTool(
                operation="search_parties",
                name="search_parties",
                fields=("sqid", "display_name", "given_name", "family_name", "nickname"),
                args=("query", "limit"),
                description="Find people whose display name contains query. Returns public party ids "
                "(pty_ sqids); use this before reads, circle membership, relationships, or merges.",
            ),
            GraphQLTool(
                operation="parties_by_pk",
                name="read_party",
                fields=(
                    "sqid",
                    "display_name",
                    "notes",
                    ("handles", _HANDLE_SUMMARY),
                    ("circle_members", _CIRCLE_MEMBERSHIP),
                    ("relationships", _RELATIONSHIP),
                ),
                id_arg="id",
                description="Read one party by its public pty_ sqid, including contact handles, circle "
                "memberships, and typed relationships.",
            ),
            GraphQLTool(
                operation="party_handles",
                name="list_review_queue",
                fields=_IDENTITY_DECISION,
                limit_arg="limit",
                fixed={
                    "where": {
                        "confidence": {"_lt": 0.5},
                        "is_confirmed": {"_eq": False},
                        "is_dismissed": {"_eq": False},
                    }
                },
                description="List low-confidence undecided party-handle identity claims for human "
                "review. Claim ids are phl_ sqids used by the confirm/dismiss tools.",
            ),
            GraphQLTool(
                operation="confirm_party_handle",
                name="confirm_party_handle",
                fields=_IDENTITY_DECISION,
                id_arg="id",
                description="Confirm one review claim by phl_ sqid when the handle truly belongs to "
                "the proposed party; confirmation becomes the strongest identity signal.",
            ),
            GraphQLTool(
                operation="dismiss_party_handle",
                name="dismiss_party_handle",
                fields=_IDENTITY_DECISION,
                id_arg="id",
                description="Dismiss one review claim by phl_ sqid when the proposed identity is wrong; "
                "the durable anti-link prevents the same claim being suggested again.",
            ),
            GraphQLTool(
                operation="merge_parties",
                name="merge_parties",
                fields=("sqid", "display_name", "notes"),
                args=("into_id", "from_id", "field_overrides"),
                requires_user_actor=True,
                description="Merge from_id into survivor into_id (both pty_ sqids) after human review. "
                "field_overrides is an optional object containing only approved human scalar fields.",
            ),
            GraphQLTool(
                operation="insert_circles_one",
                name="create_circle",
                fields=("sqid", "name", "description", ("parent", ("sqid", "name"))),
                flatten="object",
                description="Create a private organizing circle. parent, when supplied, is a cir_ sqid; "
                "the returned circle id is also a cir_ sqid.",
            ),
            GraphQLTool(
                operation="insert_circle_members_one",
                name="add_to_circle",
                fields=_CIRCLE_MEMBERSHIP,
                flatten="object",
                description="Add a party to a circle using circle=cir_ sqid and party=pty_ sqid. Returns "
                "the new cme_ membership sqid.",
            ),
            GraphQLTool(
                operation="delete_circle_members_by_pk",
                name="remove_from_circle",
                fields=_CIRCLE_MEMBERSHIP,
                id_arg="id",
                description="Remove one circle membership by its public cme_ sqid; use the membership id, "
                "not the party or circle id.",
            ),
            GraphQLTool(
                operation="insert_party_relationships_one",
                name="create_relationship",
                fields=_RELATIONSHIP,
                flatten="object",
                description="Create a typed relationship anchored at party=pty_ sqid. Supply kind=rkd_ "
                "sqid and either other_party=pty_ sqid or other_name for an untracked counterparty.",
            ),
        ],
    )
