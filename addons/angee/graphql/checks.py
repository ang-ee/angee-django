"""Django system checks for GraphQL composition and REBAC database routing."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.db import DEFAULT_DB_ALIAS, router
from rebac.resources import model_resource_type

from angee.graphql.schema import GraphQLSchemas


def check_rebac_database(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Require REBAC-managed models to use the default write database."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        if not model_resource_type(model):
            continue
        alias = router.db_for_write(model)
        if alias != DEFAULT_DB_ALIAS:
            errors.append(
                checks.Error(
                    f"REBAC-managed model {model._meta.label} routes writes to {alias!r}; "
                    "REBAC requires the default database.",
                    hint="Update DATABASE_ROUTERS so REBAC-managed models write to 'default'.",
                    obj=model,
                    id="angee.rebac.E001",
                )
            )
    return errors


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
