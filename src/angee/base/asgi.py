"""ASGI application factory for Angee runtime hosts."""

from __future__ import annotations

import re

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.urls import re_path

from angee.base.consumers import AngeeGraphQLWSConsumer
from angee.base.graphql.schema import GraphQLSchemas


def build_application() -> ProtocolTypeRouter:
    """Return an ASGI app serving HTTP and GraphQL WebSocket traffic."""

    django_asgi_app = get_asgi_application()
    schemas = GraphQLSchemas.from_discovery()
    websocket_routes = [
        re_path(
            rf"^graphql/{re.escape(name)}/$",
            AngeeGraphQLWSConsumer.as_asgi(schema=schemas.build(name)),
        )
        for name in schemas.names()
    ]
    return ProtocolTypeRouter(
        {
            "http": django_asgi_app,
            "websocket": AuthMiddlewareStack(URLRouter(websocket_routes)),
        }
    )
