"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

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


def settings(namespace: Mapping[str, Any]) -> dict[str, str | None]:
    """Return DEBUG-sensitive GraphQL settings."""

    return {"ANGEE_GRAPHQL_IDE": "graphiql" if namespace.get("DEBUG") else None}
