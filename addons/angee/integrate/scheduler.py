"""Pure scheduler logic for due integration bridges."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.integrate.models import Bridge
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.registry import models_with

_QUEUED_RECOVERY_SECONDS = 300


def enqueue_due_bridges(*, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Claim every due bridge row and enqueue one sync task for each."""

    timestamp = now or timezone.now()
    stale_before = timestamp - timedelta(seconds=_QUEUED_RECOVERY_SECONDS)
    enqueued = 0
    skipped = 0

    with system_context(reason="integrate.scheduler"):
        for model in models_with(base=Bridge):
            alias = get_write_alias(model, using=using)
            due_ids = list(
                model._default_manager.db_manager(alias)
                .due_for_enqueue(timestamp=timestamp, stale_before=stale_before)
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            for pk in due_ids:
                with transaction.atomic(using=alias):
                    bridge = (
                        model._default_manager.db_manager(alias)
                        .due_for_enqueue(timestamp=timestamp, stale_before=stale_before)
                        .lock_if_supported()
                        .filter(pk=pk)
                        .first()
                    )
                    if bridge is None:
                        skipped += 1
                        continue
                    if bridge.sync_stage != Bridge.SyncStage.QUEUED:
                        bridge.claim_sync(now=timestamp)
                    bridge.mark_sync_queued(now=timestamp)
                try:
                    queue_bridge_sync(bridge, now=timestamp, persist=False, using=alias)
                except Exception:
                    with transaction.atomic(using=alias):
                        reset = model._default_manager.db_manager(alias).lock_if_supported().filter(pk=pk).first()
                        if reset is not None and reset.sync_queue_token_matches(timestamp):
                            reset.reset_sync_queue(now=timestamp)
                    raise
                enqueued += 1

    return {"enqueued": enqueued, "skipped": skipped}
