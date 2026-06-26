"""Signal receivers for integration-owned denormalized facts."""

from __future__ import annotations

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.db.models.signals import post_migrate


def connect() -> None:
    """Register integration signal receivers once."""

    post_migrate.connect(_sync_integration_kinds, dispatch_uid="angee.integrate.sync_integration_kinds")


def _sync_integration_kinds(*, app_config: object, using: str, **kwargs: object) -> None:
    """Backfill Integration.kind after migrations create or alter the parent table."""

    label = getattr(app_config, "label", "")
    if label != "integrate":
        return
    try:
        integration_model = apps.get_model("integrate", "Integration")
    except LookupError:
        return
    if not _table_has_column(using, integration_model._meta.db_table, "kind"):
        return
    manager = getattr(integration_model, "objects", None)
    sync_kinds = getattr(manager, "sync_kinds", None)
    if not callable(sync_kinds):
        raise ImproperlyConfigured("integrate.Integration.objects must expose sync_kinds().")
    sync_kinds()


def _table_has_column(using: str, table_name: str, column_name: str) -> bool:
    """Return whether one database table currently exposes ``column_name``."""

    connection = connections[using]
    with connection.cursor() as cursor:
        if table_name not in connection.introspection.table_names(cursor):
            return False
        return any(
            column.name == column_name
            for column in connection.introspection.get_table_description(cursor, table_name)
        )
