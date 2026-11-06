"""Channels WebSocket consumers for GraphQL subscriptions."""

from __future__ import annotations

from strawberry.channels import GraphQLWSConsumer


class AngeeGraphQLWSConsumer(
    GraphQLWSConsumer[dict[str, object], None],
):
    """GraphQL WebSocket consumer with REBAC-owned actor setup."""
