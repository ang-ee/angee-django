"""Celery task wrappers for parties' evidence-backed handle suggesters."""

from __future__ import annotations

from celery import shared_task
from django.apps import apps
from rebac import system_context

from angee.jobs.locks import LockKey, task_lock


@shared_task(
    name="parties.refresh_handle_suggestions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def refresh_handle_suggestions(timestamp: int | None = None) -> int:
    """Reconcile parties-owned suggestions with current handle and signature evidence.

    :meth:`~angee.parties.managers.PartyHandleManager.reconcile_suggestions` owns
    the evidence providers and their contract, so a rule change heals every
    deployment on the next run. Like the phone renormalization, each run first
    repairs handle owners stored under an older resolution rule.

    Every run reads every signature: whether a number is one person's own depends
    on all the mail that carries it, and the whole corpus mines in seconds. The
    advisory task lock keeps a slow sweep from stacking onto the next tick.
    """

    del timestamp
    with task_lock(LockKey("parties", ("refresh_handle_suggestions",))) as acquired:
        if not acquired:
            return 0
        return _refresh_handle_suggestions()


def _refresh_handle_suggestions() -> int:
    """Run the owner repair and suggester passes under the already-held task lock."""

    party_handles = apps.get_model("parties", "PartyHandle").objects
    with system_context(reason="parties.tasks.refresh_handle_suggestions"):
        handles = apps.get_model("parties", "Handle").objects
        changed = int(handles.renormalize_phone_values()) + int(party_handles.resolve_stale_owners())
        changed += int(party_handles.reconcile_suggestions())
    return changed
