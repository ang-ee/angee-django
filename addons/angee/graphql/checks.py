"""Django system checks for composed GraphQL schemas."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core import checks

from angee.graphql.schema import GraphQLSchemas


def check_graphql_schemas(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Build every named schema and report all invalid compositions."""

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
