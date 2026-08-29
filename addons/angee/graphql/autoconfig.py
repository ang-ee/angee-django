"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME

SETTINGS = {
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
                "CONFIG": {"hosts": [redis_url]},
            }
        }
    return derived


def _channel_redis_url(namespace: Mapping[str, Any]) -> str:
    """Return the Redis URL used for the shared Channels layer."""

    return str(
        namespace.get("CHANNEL_REDIS_URL")
        or namespace.get("REDIS_URL")
        or os.environ.get("CHANNEL_REDIS_URL")
        or os.environ.get("REDIS_URL")
        or ""
    )
