"""Post-migrate reconcile of the platform ``Addon`` reflection table.

Mirrors Django's content-type / permission sync (``create_contenttypes``): a thin
``post_migrate`` receiver that delegates the work to ``AddonManager``, honoring the
DB alias + the migration router + the not-yet-created table exactly as that pattern
does. (Angee's other derived facts use explicit post-migrate commands; this one
follows Django's signal pattern because it reflects the same kind of
composer-derived metadata content types do.)
"""

from __future__ import annotations

from django.apps import apps
from django.db import connections, router
from django.db.models.signals import post_migrate
from rebac import system_context


def connect() -> None:
    """Register the addon-reflection receiver once."""

    post_migrate.connect(_reconcile_addons, dispatch_uid="angee.platform.reconcile_addons")


def _reconcile_addons(*, app_config: object, using: str, **kwargs: object) -> None:
    """Converge the Addon table after migrations create/alter the platform app."""

    if getattr(app_config, "label", "") != "platform":
        return
    try:
        addon_model = apps.get_model("platform", "Addon")
    except LookupError:
        return
    if not router.allow_migrate_model(using, addon_model):
        return
    if not _table_exists(using, addon_model._meta.db_table):
        return  # not yet created (e.g. migrating back past the Addon migration)
    with system_context(reason="platform.reconcile_addons"):
        addon_model.objects.reconcile_from_registry(using)


def _table_exists(using: str, table_name: str) -> bool:
    """Return whether one database currently has ``table_name``."""

    connection = connections[using]
    with connection.cursor() as cursor:
        return table_name in connection.introspection.table_names(cursor)
