"""Django config for Angee's GraphQL runtime."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks
from sentry_sdk.integrations.logging import ignore_logger

from angee.base.hooks import check_hooks

check_settings = partial(check_hooks, ("ANGEE_GRAPHQL_RESOURCE_FILTERS",))


class GraphQLConfig(AppConfig):
    """Wire GraphQL-owned process-local hooks after app population."""

    default = True
    name = "angee.graphql"
    compose_urls = "angee.graphql.urls"
    requires_apps = ('channels', 'django.contrib.auth', 'django.contrib.sessions')

    def compose_asgi(self):
        """Contribute the single Channels protocol owner after app population."""

        from django_compose.asgi import AsgiContribution

        from angee.graphql.asgi import websocket_application

        return AsgiContribution(protocols={"websocket": websocket_application()})

    def ready(self) -> None:
        """Register schema checks and connect model-change hooks."""

        super().ready()
        # The schema sanitizer owns the one diagnostic event without resolver
        # values; Strawberry's ordinary log must not send a second Sentry event.
        ignore_logger("strawberry.execution")
        # Phase-1 AppConfig loading imports this module before schema declarations
        # and concrete runtime models are safe to resolve; defer these imports until
        # Django calls ready() after app population.
        # Base value fields cannot import GraphQL, so their field types register here
        # before any schema resource resolves them.
        from angee.graphql import field_types  # noqa: F401
        from angee.graphql.checks import check_graphql_schemas
        from angee.graphql.schema import GraphQLSchemas

        checks.register(check_settings, checks.Tags.models)

        checks.register(check_graphql_schemas)
        GraphQLSchemas.from_discovery().connect_change_publishers()
