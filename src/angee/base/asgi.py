"""ASGI application serving GraphQL over HTTP and WebSockets.

HTTP requests are handled by the Django ASGI app, so they pass through Django
middleware and reach the ``graphql/<name>/`` views in ``angee.base.urls``.
WebSocket connections for each contributed schema are routed to a Strawberry
Channels consumer (``angee.base.consumers``), one route per schema name. Names
are enumerated when the application is built; serving happens after the runtime
is composed, so every addon ``graphql.py`` is importable at that point.
"""

from __future__ import annotations

import re

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import re_path

from angee.base.consumers import AngeeGraphQLWSConsumer
from angee.base.graphql.schema import build_schema, collect_schema_names


def build_application() -> ProtocolTypeRouter:
    """Return an ASGI app serving GraphQL over HTTP and WebSockets."""

    django_asgi_app = get_asgi_application()
    websocket_routes = [
        re_path(
            rf"^graphql/{re.escape(name)}/$",
            AngeeGraphQLWSConsumer.as_asgi(schema=build_schema(name)),
        )
        for name in collect_schema_names()
    ]
    return ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_routes)),
        }
    )
