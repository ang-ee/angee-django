"""Celery task wrappers for the nexus recompute pass."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from rebac import system_context


@shared_task(
    name="nexus.recompute_ties",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def recompute_ties(timestamp: int | None = None) -> int:
    """Recompute every derived party-pair edge."""

    del timestamp
    tie_model = apps.get_model("nexus", "Tie")
    with system_context(reason="nexus.tasks.recompute_ties"):
        return int(tie_model.objects.recompute())
