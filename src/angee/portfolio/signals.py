"""Keep health projections consistent through Django's deletion collector."""

from typing import Any

from django.db.models.signals import post_delete, pre_delete
from rebac import system_context

from angee.base.signals import connect_for_models
from angee.portfolio.models import Update


def lock_report_target(sender: Any, instance: Any, using: str, **kwargs: Any) -> None:
    """Serialize report removal with report insertion and edits."""

    with system_context(reason="portfolio.update.delete.target"):
        target = instance.target
        if target is not None:
            type(target).system_queryset(lock=("self",)).using(using).filter(pk=target.pk).first()


def refresh_report_target(sender: Any, instance: Any, using: str, **kwargs: Any) -> None:
    """Recompute from surviving reports after instance, queryset or cascade deletion."""

    with system_context(reason="portfolio.update.delete.latest"):
        target = instance.target
        if target is not None:
            type(instance).refresh_target_health(target, using=using)


def connect() -> None:
    """Install native collector hooks for composed Update descendants."""

    connect_for_models(
        pre_delete, lock_report_target, applies=lambda model: issubclass(model, Update),
        dispatch_uid="portfolio.update.lock_target",
    )
    connect_for_models(
        post_delete, refresh_report_target, applies=lambda model: issubclass(model, Update),
        dispatch_uid="portfolio.update.refresh_target",
    )
