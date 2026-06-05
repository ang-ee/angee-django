"""Django app config for the Angee GraphQL runtime."""

from __future__ import annotations

from django.apps import AppConfig


class GraphQLConfig(AppConfig):
    """GraphQL runtime and schema command host."""

    default = True
    name = "angee.graphql"
    depends_on = ("angee.base", "channels", "daphne")
    schemas = None
