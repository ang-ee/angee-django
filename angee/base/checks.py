"""Django system checks for Angee's runtime persistence contracts."""

from __future__ import annotations

from collections.abc import Sequence

from django.apps import AppConfig, apps
from django.core import checks
from django.db import DEFAULT_DB_ALIAS, router
from rebac.models import RebacResource, Relationship, RelationshipRegistry
from rebac.resources import model_resource_type

from angee.base.mixins import HierarchyQuerySet


def check_hierarchy_queryset_order(
    app_configs: Sequence[AppConfig] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Reject queryset ordering that skips another guard during path rewrites."""

    del kwargs
    models = (
        apps.get_models()
        if app_configs is None
        else (model for config in app_configs for model in config.get_models())
    )
    errors: list[checks.CheckMessage] = []
    for model in models:
        for manager in model._meta.managers:
            queryset_type = type(manager.get_queryset())
            if not issubclass(queryset_type, HierarchyQuerySet):
                continue
            preceding = queryset_type.__mro__[:queryset_type.__mro__.index(HierarchyQuerySet)]
            if any(not issubclass(owner, HierarchyQuerySet) or "update" in owner.__dict__ for owner in preceding):
                errors.append(
                    checks.Error(
                        f"{model._meta.label}.{manager.name} must compose HierarchyQuerySet "
                        "before every other queryset guard.",
                        hint="Put HierarchyQuerySet first and define update guards in a following queryset base.",
                        obj=model,
                        id="angee.E021",
                    )
                )
    return errors


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
        if model not in (Relationship, RelationshipRegistry, RebacResource) and not model_resource_type(model):
            continue
        alias = router.db_for_write(model)
        if alias != DEFAULT_DB_ALIAS:
            errors.append(
                checks.Error(
                    f"REBAC-managed model {model._meta.label} routes writes to {alias!r}; "
                    "REBAC requires the default database.",
                    hint="Update DATABASE_ROUTERS so REBAC-managed models write to 'default'.",
                    obj=model,
                    id="angee.E020",
                )
            )
    return errors
