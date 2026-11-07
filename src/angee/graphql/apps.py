"""Django config for Angee's GraphQL runtime."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core import checks
from sentry_sdk.integrations.logging import ignore_logger

from angee.base.hooks import check_hooks


def check_settings(app_configs: Sequence[AppConfig] | None = None, **kwargs: object) -> list[checks.CheckMessage]:
    """Validate GraphQL's hook settings, then resolve every related count declaration and target."""

    errors = check_hooks(("ANGEE_GRAPHQL_RESOURCE_FILTERS", "ANGEE_GRAPHQL_RELATED_COUNTS"), app_configs, **kwargs)
    if errors:
        return errors
    from angee.graphql.related_counts import check_related_counts

    return check_related_counts()


class GraphQLConfig(AppConfig):
    """Wire GraphQL-owned process-local hooks after app population."""

    default = True
    name = "angee.graphql"
    requires_apps = ('channels', 'django.contrib.auth', 'django.contrib.sessions')

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
        from angee.graphql.checks import check_graphql_channel_layer, check_graphql_schemas
        from angee.graphql.publishing import connect_change_publishers

        checks.register(check_settings, checks.Tags.models)

        checks.register(check_graphql_schemas)
        checks.register(check_graphql_channel_layer)
        if getattr(settings, "COMPOSE_SNAPSHOT", None) is not None and mode() is None:
            connect_change_publishers()
        # Compose connects in SchemaIntegration after all ready callbacks. Bare
        # source-model hosts wire their explicit projection in test fixtures.
