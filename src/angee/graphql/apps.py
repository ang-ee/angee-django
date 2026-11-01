"""Django config for Angee's GraphQL runtime."""

from __future__ import annotations

from functools import partial

from django.apps import AppConfig
from django.core import checks
from sentry_sdk.integrations.logging import ignore_logger

from angee.base.hooks import check_hook, check_hooks

check_settings = partial(
    check_hooks,
    (
        "ANGEE_GRAPHQL_RESOURCE_FILTERS",
        "ANGEE_GRAPHQL_RESOURCE_CONTRIBUTORS",
        "ANGEE_GRAPHQL_RESOURCE_METADATA",
        "ANGEE_GRAPHQL_CHANGE_PROJECTORS",
    ),
)


class GraphQLConfig(AppConfig):
    """Wire GraphQL-owned process-local hooks after app population."""

    default = True
    name = "angee.graphql"
    compose_urls = "angee.graphql.urls"
    requires_apps = ("channels",)

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
        from django.conf import settings
        from django_compose.serving import mode

        from angee.graphql import field_types  # noqa: F401
        from angee.graphql.checks import check_graphql_schemas
        from angee.graphql.publishing import connect_change_adapter, connect_change_publishers

        connect_change_adapter()
        checks.register(check_settings, checks.Tags.models)
        for name in (
            "ANGEE_GRAPHQL_SCHEMA_CLASS",
            "ANGEE_GRAPHQL_HTTP_VIEW_CLASS",
            "ANGEE_GRAPHQL_WS_CONSUMER_CLASS",
            "ANGEE_GRAPHQL_CHANGE_FILTER",
        ):
            checks.register(partial(check_hook, name), checks.Tags.models)

        checks.register(check_graphql_schemas)
        if getattr(settings, "COMPOSE_SNAPSHOT", None) is not None and mode() is None:
            connect_change_publishers()
        # Compose connects in SchemaIntegration after all ready callbacks. Bare
        # source-model hosts wire their explicit projection in test fixtures.
