"""Django config for Angee's GraphQL runtime."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class GraphQLConfig(AppConfig):
    """Wire GraphQL-owned process-local hooks after app population."""

    default = True
    name = "angee.graphql"

    def ready(self) -> None:
        """Register schema checks and connect model-change publishers and receivers."""

        super().ready()
        # Phase-1 AppConfig loading imports this module before schema declarations
        # and concrete runtime models are safe to resolve; defer these imports until
        # Django calls ready() after app population.
        from angee.graphql.checks import check_graphql_schemas
        from angee.graphql.publishing import connect_change_broadcast_receiver
        from angee.graphql.schema import GraphQLSchemas

        checks.register(check_graphql_schemas)
        connect_change_broadcast_receiver()
        GraphQLSchemas.from_discovery().connect_change_publishers()
