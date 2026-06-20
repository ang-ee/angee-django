"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

from angee.graphql.constants import PUBLIC_ID_FIELD_NAME

SETTINGS = {
    "ANGEE_GRAPHQL_IDE": "graphiql",
    "STRAWBERRY_DJANGO:append": {
        "DEFAULT_PK_FIELD_NAME": PUBLIC_ID_FIELD_NAME,
        "MAP_AUTO_ID_AS_GLOBAL_ID": False,
    },
    "CHANNEL_LAYERS:append": {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
}
"""Django settings contributed when GraphQL is installed."""
