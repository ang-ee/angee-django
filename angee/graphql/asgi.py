"""WebSocket URL routes for GraphQL subscriptions."""

from __future__ import annotations

import re

from django.urls import re_path

from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.schema import GraphQLSchemas


def websocket_urlpatterns() -> list[object]:
    """Return GraphQL WebSocket URL routes for installed schemas."""

    schemas = GraphQLSchemas.from_discovery()
    return [
        re_path(
            rf"^graphql/{re.escape(name)}/$",
            AngeeGraphQLWSConsumer.as_asgi(schema=schemas.build(name)),
        )
        for name in schemas.names()
    ]
