"""Django system checks for GraphQL composition."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

from angee.base.hooks import check_hook
from angee.graphql.impl import IMPL_CHOICES_ADMISSION
from angee.graphql.publishing import validate_channel_layer


def check_graphql_channel_layer(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Report unsafe process-local change delivery before the first publish.

    ``GraphQLConfig`` registers it under ``angee.server``'s ``SERVING_TAG``:
    build and data commands deliver no changes to subscribers.
    """

    del app_configs, kwargs
    try:
        validate_channel_layer()
    except ImproperlyConfigured as error:
        return [checks.Error(
            str(error),
            hint="Configure Redis-backed CHANNEL_LAYERS for worker broadcasts, or run DEBUG without a Celery broker.",
            id="angee.graphql.E003",
        )]
    return []


def check_graphql_schemas(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Build every named schema and report all invalid compositions."""

    from angee.graphql.schema import GraphQLSchemas

    del app_configs, kwargs
    try:
        schemas = GraphQLSchemas.from_discovery()
        names = schemas.names()
    except Exception as error:  # noqa: BLE001 - checks report invalid composition instead of aborting
        return [
            checks.Error(
                f"GraphQL schema discovery failed: {error}",
                hint="Fix the owning addon manifest or schema declaration, then rerun manage.py check.",
                id="angee.graphql.E001",
            )
        ]

    errors: list[checks.CheckMessage] = []
    for name in names:
        try:
            schemas.build(name)
        except Exception as error:  # noqa: BLE001 - retain successful builds and report every invalid bucket
            errors.append(
                checks.Error(
                    f"GraphQL schema {name!r} failed to build: {error}",
                    hint="Fix the owning addon schema contribution, then rerun manage.py check.",
                    id="angee.graphql.E002",
                )
            )
    return errors


def check_impl_choices_admission(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Validate the ``ANGEE_IMPL_CHOICES_ADMISSION`` hook under the contract its caller resolves it by.

    Optional and single (``check_hook``): unset is valid, and then only a model's own policy admits a
    choices read; a path that does not import or is not callable is ``angee.E033``.
    """

    return check_hook(IMPL_CHOICES_ADMISSION, app_configs, **kwargs)
