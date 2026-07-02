"""Signal receivers for integration-owned denormalized facts."""

from __future__ import annotations

from django.apps import apps
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
    integration_model.objects.db_manager(using).sync_kinds()
