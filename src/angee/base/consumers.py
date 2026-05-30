"""Channels WebSocket consumer for GraphQL subscriptions.

WebSocket connections bypass Django middleware, so the connection actor is
resolved from the channels scope here and attached to the GraphQL context for
subscription read-gating. ``angee.base.asgi`` routes one consumer per schema.
"""

from __future__ import annotations

from typing import Any

from strawberry.channels import GraphQLWSConsumer

from angee.base.graphql.subscriptions import scope_actor


class AngeeGraphQLWSConsumer(GraphQLWSConsumer[dict[str, object], None]):
    """GraphQL WebSocket consumer that resolves the REBAC actor on connect.

    HTTP requests get their actor from ``rebac.middleware.ActorMiddleware``;
    WebSocket connections bypass Django middleware, so the connection actor is
    resolved from the channels scope and attached to the GraphQL context for
    subscription read-gating.
    """

    async def get_context(
        self, request: Any, response: Any
    ) -> dict[str, object]:
        context = await super().get_context(request, response)
        context["actor"] = scope_actor(self.scope)
        return context
