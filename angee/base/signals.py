"""Rebuild the local permission index after migrations that bypass model writes."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.db import router
from django.db.migrations.operations.special import RunPython, RunSQL
from django.db.models.signals import post_migrate
from rebac import app_settings
from rebac.models import active_relationship_model
from rebac.models.generation import SchemaGeneration


def connect_permission_index_rebuild() -> None:
    """Watch completed rebac migrations once per process."""

    post_migrate.connect(
        rebuild_permission_index,
        sender=apps.get_app_config("rebac"),
        dispatch_uid="angee.base.rebuild_permission_index",
    )


def rebuild_permission_index(*, using: str, plan: list[tuple[Any, bool]] | None = None, **_: Any) -> None:
    """Repair a ready index when data migrations or SQL may have bypassed tracking."""

    if app_settings.REBAC_BACKEND != "local":
        return
    if not router.allow_migrate_model(using, active_relationship_model()):
        return
    if not any(
        isinstance(operation, (RunPython, RunSQL))
        for migration, _ in (plan or ())
        for operation in migration.operations
    ):
        return
    pair = SchemaGeneration.objects.revision_pair(using)
    if pair is None or not pair[0] or pair[0] != pair[1]:
        return
    call_command("rebac", "index", "rebuild", database=using, verbosity=0)
