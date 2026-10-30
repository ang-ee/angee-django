"""ASGI subscription routes for prebuilt GraphQL deployments."""

from __future__ import annotations

import re
from typing import cast

from asgiref.typing import ASGI3Application
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path
from django_compose.components import installed_components

from angee.base.addons import addon_contribution
from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.middleware import WebsocketOriginValidator
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


def websocket_application() -> ASGI3Application:
    """Compose installed WebSocket routes with native Channels auth and origin policy."""

    from channels.auth import AuthMiddlewareStack
    from channels.routing import URLRouter

    configs = tuple(apps.get_app_configs())
    index = installed_components("angee.addons")
    patterns = [
        pattern for config in configs
        for pattern in addon_contribution(config, "asgi", "websocket_urlpatterns", allow_callable=True, index=index)
    ]
    application = cast(ASGI3Application, URLRouter(patterns))
    wrappers = [
        wrapper for config in configs
        for wrapper in addon_contribution(config, "asgi", "websocket_middleware", index=index)
    ]
    for wrapper in reversed(wrappers):
        if not callable(wrapper):
            raise ImproperlyConfigured("asgi.websocket_middleware must contain callables")
        application = wrapper(application)
    return WebsocketOriginValidator(AuthMiddlewareStack(application))
