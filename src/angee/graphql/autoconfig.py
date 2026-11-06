"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sentry_sdk.integrations.strawberry import StrawberryIntegration

from angee.graphql.constants import CHANGE_GROUP_EXPIRY_SECONDS, PUBLIC_ID_FIELD_NAME

SETTINGS = {
    "COMPOSE_INTEGRATIONS:append": ["angee.graphql.sdl.SchemaIntegration"],
    "COMPOSE_EXTERNAL_ARTIFACT_ROOTS:append": ["schemas"],
    # Native HTTP error capture sees coercion values before our safe log policy.
    "SENTRY_DISABLED_INTEGRATIONS:append": [StrawberryIntegration],
    "ANGEE_GRAPHQL_RESOURCE_FILTERS": [],
    # Owners append dotted callables returning their related groups' record
    # edges; see ``angee.graphql.edges``.
    "ANGEE_GRAPHQL_RECORD_EDGES": [],
    "STRAWBERRY_DJANGO:append": {
        "DEFAULT_PK_FIELD_NAME": PUBLIC_ID_FIELD_NAME,
        "MAP_AUTO_ID_AS_GLOBAL_ID": False,
    },
    "CHANNEL_LAYERS:append": {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
}
"""Django settings contributed when GraphQL is installed."""


def settings(namespace: Mapping[str, Any]) -> dict[str, str | None | dict[str, Any]]:
    """Return DEBUG-sensitive GraphQL settings."""

    derived: dict[str, str | None | dict[str, Any]] = {
        "ANGEE_GRAPHQL_IDE": "graphiql" if namespace.get("DEBUG") else None
    }
    redis_url = _channel_redis_url(namespace)
    if redis_url:
        derived["CHANNEL_LAYERS:append"] = {
            "default": {
                "BACKEND": "channels_redis.core.RedisChannelLayer",
                "CONFIG": {"hosts": [redis_url], "group_expiry": CHANGE_GROUP_EXPIRY_SECONDS},
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
