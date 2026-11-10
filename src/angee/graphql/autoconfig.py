"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict

from sentry_sdk.integrations.strawberry import StrawberryIntegration

from angee.graphql.constants import (
    CHANGE_GROUP_EXPIRY_SECONDS,
    CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS,
    PUBLIC_ID_FIELD_NAME,
)

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER": bool,
        "ANGEE_GRAPHQL_EXTENSION_FACTORIES": tuple[str, ...],
        "COMPOSE_INTEGRATIONS:append": list[str],
        "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": list[str],
        "SENTRY_DISABLED_INTEGRATIONS:append": list[type[StrawberryIntegration]],
        "ANGEE_GRAPHQL_RESOURCE_FILTERS": list[str],
        "ANGEE_GRAPHQL_RELATED_COUNTS": list[str],
        "STRAWBERRY_DJANGO:append": dict[str, Any],
        "CHANNEL_LAYERS:append": dict[str, Any],
    },
)

_DerivedSettings = TypedDict(
    "_DerivedSettings",
    {"ANGEE_GRAPHQL_IDE": str | None, "CHANNEL_LAYERS:append": dict[str, Any]},
    total=False,
)

SETTINGS: _Settings = {
    "ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER": False,
    "ANGEE_GRAPHQL_EXTENSION_FACTORIES": (),
    "COMPOSE_INTEGRATIONS:append": ["angee.graphql.sdl.SchemaIntegration"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["schemas"],
    # Native HTTP error capture sees coercion values before our safe log policy.
    "SENTRY_DISABLED_INTEGRATIONS:append": [StrawberryIntegration],
    "ANGEE_GRAPHQL_RESOURCE_FILTERS": [],
    # Owners append dotted callables returning their related groups' count
    # declarations; see ``angee.graphql.related_counts``.
    "ANGEE_GRAPHQL_RELATED_COUNTS": [],
    "STRAWBERRY_DJANGO:append": {
        "DEFAULT_PK_FIELD_NAME": PUBLIC_ID_FIELD_NAME,
        "MAP_AUTO_ID_AS_GLOBAL_ID": False,
    },
    "CHANNEL_LAYERS:append": {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
}
"""Django settings contributed when GraphQL is installed."""


def settings(namespace: Mapping[str, Any]) -> _DerivedSettings:
    """Return DEBUG-sensitive GraphQL settings."""

    derived: _DerivedSettings = {
        "ANGEE_GRAPHQL_IDE": "graphiql" if namespace.get("DEBUG") else None
    }
    redis_url = _channel_redis_url(namespace)
    if redis_url:
        derived["CHANNEL_LAYERS:append"] = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {
                    "hosts": [{"address": redis_url, "socket_timeout": CHANNEL_LAYER_SOCKET_TIMEOUT_SECONDS}],
                    "group_expiry": CHANGE_GROUP_EXPIRY_SECONDS,
                },
            }
        }
    return derived


def _channel_redis_url(namespace: Mapping[str, Any]) -> str:
    """Prefer a channel URL, then the stack Redis URL or its Redis broker."""

    url = namespace.get("CHANNEL_REDIS_URL") or namespace.get("REDIS_URL")
    if url:
        return str(url)
    broker_url = str(namespace.get("CELERY_BROKER_URL") or "")
    return broker_url if broker_url.startswith(("redis://", "rediss://")) else ""
