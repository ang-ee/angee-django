"""Per-model receiver binding, and the permission index rebuild after bypassing migrations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.apps import apps
from django.core.management import call_command
from django.db import router
from django.db.migrations.operations.special import RunPython, RunSQL
from django.db.models import Model
from django.db.models.signals import ModelSignal, class_prepared, post_migrate
from rebac import app_settings
from rebac.models import active_relationship_model
from rebac.models.generation import SchemaGeneration


def connect_for_models(
    signal: ModelSignal,
    receiver: Callable[..., Any],
    *,
    applies: Callable[[type[Model]], bool],
    dispatch_uid: str,
) -> None:
    """Connect ``receiver`` to each concrete model ``applies`` admits, now and as models prepare.

    A model signal receiver connected without a sender listens to every model, and a
    delete receiver of that kind stops Django from deleting any table's rows in bulk:
    the collector loads each row to send it. Owners bind to the models they concern
    instead. Proxies bind too, since a delete through a proxy queryset sends the proxy.
    Historical migration models never bind.
    """

    def bind(model: type[Model]) -> None:
        if model._meta.abstract or model._meta.apps is not apps or not applies(model):
            return
        signal.connect(receiver, sender=model, dispatch_uid=f"{dispatch_uid}.{model._meta.label_lower}")

    for model in apps.get_models():
        bind(model)
    # Models prepared after app population (test-defined records, for one) bind as they finalize.
    class_prepared.connect(
        lambda sender, **_kwargs: bind(sender), dispatch_uid=f"{dispatch_uid}.class_prepared", weak=False,
    )


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
