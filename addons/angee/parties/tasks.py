"""Celery task wrappers for parties' evidence-backed handle suggesters."""

from __future__ import annotations

from itertools import groupby
from operator import itemgetter
from typing import Any

from celery import shared_task
from django.apps import apps
from rebac import system_context


@shared_task(
    name="parties.refresh_handle_suggestions",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def refresh_handle_suggestions(timestamp: int | None = None) -> int:
    """Refresh parties-owned suggestions from current handle and signature evidence.

    Display-name evidence is native to parties. Signature evidence is an optional
    contribution from the downstream messaging addon: the app-registry guard keeps
    parties independently installable, and the task passes only neutral fragment
    text/hash plus resolved sender-party ids into the manager owner. Both passes
    partition evidence by the parties audit owner before making any inference.
    """

    del timestamp
    party_handles = apps.get_model("parties", "PartyHandle").objects
    with system_context(reason="parties.tasks.refresh_handle_suggestions"):
        handles = apps.get_model("parties", "Handle").objects
        changed = int(handles.renormalize_phone_values())
        created = int(party_handles.suggest_from_display_names())
        if not apps.is_installed("angee.messaging"):
            return changed + created

        part_model = apps.get_model("messaging", "Part")
        rows = (
            part_model._base_manager.filter(
                role="signature",
                fragment__isnull=False,
                message__sender__party__isnull=False,
            )
            .order_by(
                "message__sender__party__created_by_id",
                "fragment_id",
                "message__sender__party_id",
            )
            .values(
                "fragment_id",
                "fragment__hash",
                "fragment__text",
                "message__sender__party_id",
                "message__sender__party__created_by_id",
            )
            .exclude(message__sender__party__created_by_id=None)
            .distinct()
        )
        owner_fragment = itemgetter(
            "message__sender__party__created_by_id",
            "fragment_id",
        )
        for (owner_id, _), fragment_rows_iter in groupby(rows, key=owner_fragment):
            fragment_rows: tuple[dict[str, Any], ...] = tuple(fragment_rows_iter)
            evidence = fragment_rows[0]
            party_ids = tuple(dict.fromkeys(row["message__sender__party_id"] for row in fragment_rows))
            created += int(
                party_handles.suggest_from_signature(
                    text=evidence["fragment__text"],
                    fragment_hash=evidence["fragment__hash"],
                    party_ids=party_ids,
                    owner_id=owner_id,
                )
            )
    return changed + created
