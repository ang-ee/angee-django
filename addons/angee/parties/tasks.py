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
    """Refresh parties-owned suggestions from current handle and signature evidence.

    Display-name evidence is native to parties. The downstream messaging addon
    optionally contributes signature evidence and its automated senders, whose
    display names are no evidence: the app-registry guard keeps parties
    independently installable, and the task passes only neutral fragment
    text/hashes, sender, party and owner ids into the manager owner. Both passes
    partition evidence by audit owner before making any inference. Like the phone
    renormalization, each run first repairs handle owners stored under an older
    resolution rule.

    Every run reads every signature: whether a number is one sender's own depends
    on all the mail that carries it, and the whole corpus mines in seconds. After
    an extraction-rule change, retract that rule's unreviewed suggestions
    (``PartyHandle.objects.retract_suggestions``) and the next run re-proposes
    what the current rule supports. The advisory task lock keeps a slow sweep from
    stacking onto the next tick.
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
        if not apps.is_installed("angee.messaging"):
            return changed + int(party_handles.suggest_from_display_names())

        automated_senders = apps.get_model("messaging", "Message").objects.automated_sender_ids()
        created = int(party_handles.suggest_from_display_names(shared_handle_ids=automated_senders))
        signings = (
            apps.get_model("messaging", "Part")
            ._base_manager.filter(role="signature", fragment__isnull=False, message__sender__isnull=False)
            .order_by("fragment__hash", "message__sender_id")
            .values_list(
                "fragment__hash",
                "fragment__text",
                "message__sender_id",
                "message__sender__party_id",
                "message__created_by_id",
            )
            .distinct()
        )
        created += int(party_handles.suggest_from_signatures(signings.iterator()))
    return changed + created
