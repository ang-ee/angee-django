"""ASGI subscription routes for prebuilt GraphQL deployments."""

from __future__ import annotations

import re
from typing import cast

from asgiref.typing import ASGI3Application
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path

from angee.base.addons import addon_contribution, addon_modules
from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.middleware import WebsocketOriginValidator
from angee.graphql.schema import GraphQLSchemas


def protocols() -> dict[str, ASGI3Application]:
    """Contribute the single Channels WebSocket owner to ``angee.server.asgi``."""

    return {"websocket": websocket_application()}


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


def websocket_application() -> ASGI3Application:
    """Compose installed WebSocket routes with native Channels auth and origin policy."""

    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter

    patterns: list[object] = []
    wrappers: list[object] = []
    for _config, module in addon_modules("asgi"):
        patterns.extend(addon_contribution(module, "websocket_urlpatterns"))
        wrappers.extend(addon_contribution(module, "websocket_middleware", allow_callable=False))
    application = cast(ASGI3Application, URLRouter(patterns))
    for wrapper in reversed(wrappers):
        if not callable(wrapper):
            raise ImproperlyConfigured("asgi.websocket_middleware must contain callables")
        application = wrapper(application)
    return WebsocketOriginValidator(AuthMiddlewareStack(application))
