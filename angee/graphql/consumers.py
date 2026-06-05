"""Channels WebSocket consumers for GraphQL subscriptions."""

from __future__ import annotations

from rebac.graphql.strawberry import RebacChannelsConsumerMixin
from strawberry.channels import GraphQLWSConsumer


class AngeeGraphQLWSConsumer(
    RebacChannelsConsumerMixin,
    GraphQLWSConsumer[dict[str, object], None],
):
    """GraphQL WebSocket consumer with REBAC-owned actor setup."""
