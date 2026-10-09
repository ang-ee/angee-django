"""Celery task wrappers for parties' evidence-backed handle suggesters."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.apps import apps
from django.db.models import Count
from django.utils import timezone
from rebac import system_context

from angee.jobs.locks import LockKey, task_lock


@shared_task(
    name="parties.refresh_handle_suggestions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def refresh_handle_suggestions(
    timestamp: int | None = None,
    lookback_hours: float | None = 2.0,
) -> int:
    """Refresh parties-owned suggestions from current handle and signature evidence.

    Display-name evidence is native to parties. The downstream messaging addon
    optionally contributes signature evidence and its automated senders, whose
    display names are no evidence: the app-registry guard keeps parties
    independently installable, and the task passes only neutral fragment
    text/hashes, party ids and handle ids into the manager owner. A fragment more
    than one sender signed with is nobody's personal evidence. Both passes
    partition evidence by the parties audit owner before making any inference.
    Like the phone renormalization, each run first repairs handle owners stored
    under an older resolution rule.

    Steady-state cost stays proportional to NEW evidence: signature parts are
    mined only within ``lookback_hours`` (2× the hourly beat, so a missed tick
    still overlaps) — ``PhoneNumberMatcher`` over the full historical corpus is
    hours of work, and parts are append-only so old fragments never change.
    Pass ``None`` for the full sweep: on first install, and after an
    extraction-rule change has retracted that rule's unreviewed suggestions
    (``PartyHandle.objects.retract_suggestions``). A suggestion already made
    stays when another sender later signs with the same fragment.
    The advisory task lock keeps a slow sweep from stacking onto the next tick.
    """

    del timestamp
    with task_lock(LockKey("parties", ("refresh_handle_suggestions",))) as acquired:
        if not acquired:
            return 0
        return _refresh_handle_suggestions(lookback_hours)


def _refresh_handle_suggestions(lookback_hours: float | None) -> int:
    """Run the owner repair and suggester passes under the already-held task lock."""

    party_handles = apps.get_model("parties", "PartyHandle").objects
    with system_context(reason="parties.tasks.refresh_handle_suggestions"):
        handles = apps.get_model("parties", "Handle").objects
        changed = int(handles.renormalize_phone_values()) + int(party_handles.resolve_stale_owners())
        if not apps.is_installed("angee.messaging"):
            return changed + int(party_handles.suggest_from_display_names())

        automated_senders = apps.get_model("messaging", "Message").objects.automated_sender_ids()
        created = int(party_handles.suggest_from_display_names(shared_handle_ids=automated_senders))

        part_model = apps.get_model("messaging", "Part")
        signature_parts = part_model._base_manager.filter(role="signature", fragment__isnull=False)
        recent = signature_parts
        if lookback_hours is not None:
            recent = recent.filter(created_at__gte=timezone.now() - timedelta(hours=lookback_hours))
        # A signature is personal evidence only while one sender alone signs with it:
        # forwards and shared company boilerplate repeat a fragment under several
        # senders, resolved or not, and none of them owns its numbers.
        personal = (
            signature_parts.filter(fragment_id__in=recent.values("fragment_id"))
            .values("fragment_id")
            .annotate(senders=Count("message__sender", distinct=True))
            .filter(senders=1)
            .values("fragment_id")
        )
        rows = (
            signature_parts.filter(fragment_id__in=personal, message__sender__party__isnull=False)
            .exclude(message__sender__party__created_by_id=None)
            .order_by("fragment_id")
            .values(
                "fragment__hash",
                "fragment__text",
                "message__sender__party_id",
                "message__sender__party__created_by_id",
            )
            .distinct()
        )
        for row in rows:
            created += int(
                party_handles.suggest_from_signature(
                    text=row["fragment__text"],
                    fragment_hash=row["fragment__hash"],
                    party_id=row["message__sender__party_id"],
                    owner_id=row["message__sender__party__created_by_id"],
                )
            )
    return changed + created
