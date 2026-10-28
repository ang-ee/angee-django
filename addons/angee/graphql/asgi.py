"""ASGI contributions for GraphQL subscriptions and development SDL emission."""

from __future__ import annotations

import re
from collections.abc import Callable

from django.conf import settings
from django.urls import re_path

from angee.graphql.consumers import AngeeGraphQLWSConsumer
from angee.graphql.schema import GraphQLSchemas
from angee.graphql.sdl import GraphQLSdl


def _emit_dev_sdl() -> None:
    """Regenerate GraphQL SDL at ASGI boot when Django DEBUG is enabled."""

    if settings.DEBUG:
        GraphQLSdl.from_discovery().emit_if_stale()


boot_hooks: tuple[Callable[[], None], ...] = (_emit_dev_sdl,)
"""ASGI boot hooks owned by the GraphQL addon."""


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
