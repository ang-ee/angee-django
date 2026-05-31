"""Channels WebSocket consumers for GraphQL subscriptions."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from rebac import SubjectRef, actor_context, anonymous_actor
from rebac.actors import get_actor_resolver
from strawberry.channels import GraphQLWSConsumer


class AngeeGraphQLWSConsumer(GraphQLWSConsumer[dict[str, object], None]):
    """GraphQL WebSocket consumer that attaches a REBAC actor."""

    async def get_context(
        self,
        request: Any,
        response: Any,
    ) -> dict[str, object]:
        """Return Strawberry context with the connection actor attached."""

        context = await super().get_context(request, response)
        context["actor"] = scope_actor(self.scope)
        return context

    async def execute_operation(
        self,
        request: Any,
        context: Any,
        root_value: Any | None,
        sub_response: Any,
    ) -> Any:
        """Execute one WebSocket GraphQL operation with an ambient actor."""

        actor = (
            context.get("actor")
            if isinstance(context, Mapping)
            else None
        ) or scope_actor(self.scope)
        with actor_context(actor):
            return await super().execute_operation(
                request,
                context,
                root_value,
                sub_response,
            )


def scope_actor(scope: Mapping[str, Any]) -> SubjectRef:
    """Resolve the REBAC actor for a Channels connection scope."""

    request = SimpleNamespace(user=scope.get("user"))
    return get_actor_resolver()(request) or anonymous_actor()
