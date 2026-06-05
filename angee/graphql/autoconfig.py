"""Settings fragments required by the Angee GraphQL runtime."""

from __future__ import annotations

SETTINGS = {
    "ANGEE_GRAPHQL_IDE": "graphiql",
    "CHANNEL_LAYERS:append": {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    },
}
"""Django settings contributed when GraphQL is installed."""
