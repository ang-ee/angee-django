"""Channels WebSocket consumers for GraphQL subscriptions."""

from __future__ import annotations

from rebac.graphql.strawberry import RebacChannelsConsumerMixin
from strawberry.channels import GraphQLWSConsumer

from angee.base.serialization import decode_json


class AngeeGraphQLWSConsumer(
    RebacChannelsConsumerMixin,
    GraphQLWSConsumer[dict[str, object], None],
):
    """GraphQL WebSocket consumer with REBAC-owned actor setup."""

    def decode_json(self, data: str | bytes) -> object:
        """Decode through ``decode_json``, so a message past ``MAX_JSON_DOCUMENT_DEPTH`` is not JSON to Strawberry."""

        return decode_json(data)
