"""Django system checks for GraphQL composition."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig
from django.core import checks
from django.core.exceptions import ImproperlyConfigured

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


def check_display_fields(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Refuse display fields the read-through-parent switch would show past a label.

    Inert unless relation fields ask for display rows
    (:func:`~angee.base.permissions.display_rows_requested`). For every model whose
    ``Meta.rebac_display_fields`` declares them (REBAC's ``rebac.E026`` refuses a
    relation, or a name that is not one of the model's columns):

    - ``angee.graphql.E005``: a declared field carries a read gate in the composed
      policy. A label is an ungated column; the gate decides the field.
    - ``angee.graphql.E006``: a plain strawberry-django relation field reaches the
      model (:func:`~angee.graphql.relations.display_audience`). REBAC joins it, and
      while ``REBAC_DISPLAY_FIELDS`` is on it delivers a joined row of the model with
      its display fields alone, unchecked, to every reader of the holder: the read
      that checked the row before shows the label instead, whole rows included.

    Schema discovery failures are ``angee.graphql.E001``'s.
    """

    from django.apps import apps

    from angee.base.permissions import declared_rebac_definition, display_rows_requested
    from angee.graphql.access import is_gated_read_axis
    from angee.graphql.relations import display_audience
    from angee.graphql.schema import GraphQLSchemas

    del app_configs, kwargs
    if not display_rows_requested():
        return []
    declared = [
        (model, tuple(fields))
        for model in apps.get_models()
        if (fields := getattr(model._meta, "rebac_display_fields", None))
    ]
    if not declared:
        return []
    try:
        schemas = GraphQLSchemas.from_discovery()
    except Exception:  # noqa: BLE001 - angee.graphql.E001 reports a failed discovery
        return []
    errors: list[checks.CheckMessage] = []
    for model, fields in declared:
        gated = [name for name in fields if is_gated_read_axis(model, name, definition_for=declared_rebac_definition)]
        if gated:
            errors.append(checks.Error(
                f"{model._meta.label} declares display fields {gated} that carry a read gate.",
                hint="A label is an ungated column: remove them from Meta.rebac_display_fields.",
                obj=model,
                id="angee.graphql.E005",
            ))
        if plain := [
            f"{entry.schema} {entry.type_name}.{entry.field}"
            for entry in display_audience(model, schemas)
            if entry.kind == "plain"
        ]:
            errors.append(checks.Error(
                f"{model._meta.label} declares display fields and plain relation fields reach it: {', '.join(plain)}. "
                "REBAC delivers the rows they join as display rows, unchecked, to every reader.",
                hint="Build each with angee.graphql.relations.actor_scoped_to_one, or remove the declaration.",
                obj=model,
                id="angee.graphql.E006",
            ))
    return errors
