"""ASGI subscription routes for prebuilt GraphQL deployments."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import cast

from asgiref.typing import ASGI3Application
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.urls import re_path
from django_compose.components import installed_components

from angee.base.addons import optional_addon_module
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
    patterns: list[object] = []
    wrappers: list[object] = []
    for config in configs:
        module = optional_addon_module(config, "asgi", index=index)
        if module is None:
            continue
        for attr, allow_callable, target in (
            ("websocket_urlpatterns", True, patterns),
            ("websocket_middleware", False, wrappers),
        ):
            if not hasattr(module, attr):
                continue
            contribution = getattr(module, attr)
            value = contribution() if allow_callable and callable(contribution) else contribution
            if not isinstance(value, Iterable) or isinstance(value, str | bytes | Mapping | set | frozenset):
                suffix = "iterable or callable" if allow_callable else "iterable"
                raise ImproperlyConfigured(f"{module.__name__}.{attr} must be {suffix}")
            target.extend(value)
    application = cast(ASGI3Application, URLRouter(patterns))
    for wrapper in reversed(wrappers):
        if not callable(wrapper):
            raise ImproperlyConfigured("asgi.websocket_middleware must contain callables")
        application = wrapper(application)
    return WebsocketOriginValidator(AuthMiddlewareStack(application))
