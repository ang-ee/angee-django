"""Celery task wrappers owned by the framework job seam."""

from __future__ import annotations

from typing import Any

from celery import shared_task
from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from rebac import system_context

from angee.base.fields import FractionalRankField


@shared_task(
    name="jobs.rebalance_fractional_ranks",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def rebalance_fractional_ranks(model_label: str, context: dict[str, Any], rank_field: str) -> int:
    """Rebalance one concrete model's ranks inside a Celery-serializable context."""

    try:
        model = apps.get_model(model_label)
    except (LookupError, ValueError) as error:
        raise ImproperlyConfigured(f"Unknown fractional-rank model {model_label!r}.") from error
    try:
        field = model._meta.get_field(rank_field)
    except FieldDoesNotExist as error:
        raise ImproperlyConfigured(
            f"{model._meta.label}.{rank_field} is not a FractionalRankField."
        ) from error
    if not isinstance(field, FractionalRankField):
        raise ImproperlyConfigured(f"{model._meta.label}.{rank_field} is not a FractionalRankField.")
    with system_context(reason="jobs.rebalance_fractional_ranks"):
        return field.rebalance(context=context)
