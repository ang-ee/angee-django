"""Django config for Angee's GraphQL runtime."""

from __future__ import annotations

from django.apps import AppConfig
from django.core import checks


class GraphQLConfig(AppConfig):
    """Wire GraphQL-owned process-local hooks after app population."""

    default = True
    name = "angee.graphql"

    def ready(self) -> None:
        """Register schema/routing checks and connect model-change hooks."""

        super().ready()
        # Phase-1 AppConfig loading imports this module before schema declarations
        # and concrete runtime models are safe to resolve; defer these imports until
        # Django calls ready() after app population.
        from angee.graphql.checks import check_graphql_schemas, check_rebac_database
        from angee.graphql.publishing import connect_change_broadcast_receiver
        from angee.graphql.schema import GraphQLSchemas

        checks.register(check_graphql_schemas)
        checks.register(check_rebac_database, checks.Tags.models)
        connect_change_broadcast_receiver()
        GraphQLSchemas.from_discovery().connect_change_publishers()
