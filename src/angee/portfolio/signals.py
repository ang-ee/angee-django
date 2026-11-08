"""Keep the target health projection through Django's deletion collector."""

from typing import Any

from django.db.models.signals import post_delete, pre_delete
from rebac import system_context

from angee.base.signals import connect_for_models
from angee.portfolio.models import Update


def connect() -> None:
    """Bind the report projection receivers to every concrete report model."""

    connect_for_models(
        pre_delete,
        lock_report_target,
        applies=lambda model: issubclass(model, Update),
        dispatch_uid="portfolio.update.lock_target",
    )
    connect_for_models(
        post_delete,
        refresh_report_target,
        applies=lambda model: issubclass(model, Update),
        dispatch_uid="portfolio.update.refresh_target",
    )


def lock_report_target(sender: Any, instance: Update, **kwargs: Any) -> None:
    """Take the report's delete locks, target first, on instance, queryset and cascade deletion."""

    with system_context(reason="portfolio.update.delete.target"):
        instance.lock_for_delete()


def refresh_report_target(sender: Any, instance: Update, **kwargs: Any) -> None:
    """Project the surviving latest report onto the deleted report's target."""

    with system_context(reason="portfolio.update.delete.latest"):
        if (target := instance.target) is not None:
            type(instance).refresh_target_health(target)
