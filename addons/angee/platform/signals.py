"""Post-migrate reconcile of the platform ``Addon`` reflection table.

Mirrors Django's content-type / permission sync (``create_contenttypes``): a thin
``post_migrate`` receiver that delegates the work to ``AddonManager`` after
checking Django's migration router and whether the table has been created.
(Angee's other derived facts use explicit post-migrate commands; this one
follows Django's signal pattern because it reflects the same kind of
composer-derived metadata content types do.)
"""

from __future__ import annotations

from django.apps import apps
from django.db import connections, router
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models.signals import post_migrate
from rebac import system_context


def connect() -> None:
    """Register the addon-reflection receiver once."""

    post_migrate.connect(_reconcile_addons, dispatch_uid="angee.platform.reconcile_addons")


def _reconcile_addons(*, app_config: object, **kwargs: object) -> None:
    """Converge the Addon table after migrations create/alter the platform app."""

    if getattr(app_config, "label", "") != "platform":
        return
    try:
        addon_model = apps.get_model("platform", "Addon")
    except LookupError:
        return
    database = str(kwargs["using"])
    if not router.allow_migrate_model(database, addon_model):
        return
    if not _table_exists(connections[database], addon_model._meta.db_table):
        return  # not yet created (e.g. migrating back past the Addon migration)
    with system_context(reason="platform.reconcile_addons"):
        addon_model.objects.reconcile_loaded_registry()


def _table_exists(connection: BaseDatabaseWrapper, table_name: str) -> bool:
    """Return whether one database currently has ``table_name``."""

    with connection.cursor() as cursor:
        return table_name in connection.introspection.table_names(cursor)
