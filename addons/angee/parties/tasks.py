"""Celery task wrappers for parties' evidence-backed handle suggesters."""

from __future__ import annotations

from itertools import chain

from celery import shared_task
from django.apps import apps
from rebac import system_context

from angee.base.impl import resolve_hooks
from angee.jobs.locks import LockKey, task_lock


@shared_task(
    name="parties.refresh_handle_suggestions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def refresh_handle_suggestions(timestamp: int | None = None) -> int:
    """Reconcile parties-owned suggestions with current handle and signature evidence.

    Display-name evidence is native to parties. Downstream addons contribute the
    rest through declared hooks, so parties reads no other addon's models:
    ``ANGEE_PARTIES_SHARED_SENDERS`` callables return handles whose display names
    are no evidence (list and notification senders), and
    ``ANGEE_PARTIES_SIGNINGS`` callables yield :class:`~angee.parties.managers.Signing`
    rows. Both passes partition evidence by audit owner before any inference, and
    each withdraws the undecided suggestions its evidence no longer supports, so a
    rule change heals every deployment on the next run. Like the phone
    renormalization, each run first repairs handle owners stored under an older
    resolution rule.

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
        shared = frozenset(chain.from_iterable(hook() for hook in resolve_hooks("ANGEE_PARTIES_SHARED_SENDERS")))
        changed += int(party_handles.suggest_from_display_names(shared_handle_ids=shared))
        signings = chain.from_iterable(hook() for hook in resolve_hooks("ANGEE_PARTIES_SIGNINGS"))
        changed += int(party_handles.suggest_from_signatures(signings))
    return changed
