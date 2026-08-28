"""Pure scheduler logic for due integration bridges."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import Bridge
from angee.integrate.queue import queue_bridge_sync
from angee.integrate.registry import bridge_models
from angee.integrate.sync_runner import run_bridge_sync_job

_QUEUED_RECOVERY_SECONDS = 300


def run_due_bridges(*, now: datetime | None = None) -> dict[str, int]:
    """Run every bridge row due at ``now`` and return scheduler counters.

    Each due row is re-read and claimed under a row lock (``Bridge.claim_sync``
    pushes its ``next_sync_at`` one interval out) before the shared bridge runner
    handles locking and lifecycle telemetry. Manual syncs use the same runner
    through the queued ``sync_bridge_now`` task.
    """

    timestamp = now or timezone.now()
    ran = 0
    errors = 0

    with system_context(reason="integrate.scheduler"):
        for model in bridge_models(Bridge):
            due_ids = list(
                model._default_manager.filter(next_sync_at__lte=timestamp).order_by("pk").values_list("pk", flat=True)
            )
            for pk in due_ids:
                with transaction.atomic():
                    bridge = (
                        model._default_manager.lock_if_supported().filter(pk=pk, next_sync_at__lte=timestamp).first()
                    )
                    if bridge is None:
                        continue  # a concurrent scan already claimed it
                    bridge.claim_sync(now=timestamp)
                try:
                    sync_result = run_bridge_sync_job(model._meta.label_lower, pk, timestamp)
                except Exception:  # noqa: BLE001 — run_sync recorded the bridge failure as telemetry.
                    ran += 1
                    errors += 1
                else:
                    if not sync_result.get("skipped"):
                        ran += 1

    return {"ran": ran, "errors": errors}


def enqueue_due_bridges(*, now: datetime | None = None) -> dict[str, int]:
    """Claim every due bridge row and enqueue one sync task for each."""

    timestamp = now or timezone.now()
    stale_before = timestamp - timedelta(seconds=_QUEUED_RECOVERY_SECONDS)
    enqueued = 0
    skipped = 0

    with system_context(reason="integrate.scheduler"):
        for model in bridge_models(Bridge):
            due_ids = list(
                model._default_manager.filter(_due_or_stale_queue_filter(timestamp, stale_before))
                .order_by("pk")
                .values_list("pk", flat=True)
            )
            for pk in due_ids:
                with transaction.atomic():
                    bridge = (
                        model._default_manager.lock_if_supported()
                        .filter(pk=pk)
                        .filter(_due_or_stale_queue_filter(timestamp, stale_before))
                        .first()
                    )
                    if bridge is None:
                        skipped += 1
                        continue
                    if bridge.sync_stage != Bridge.SyncStage.QUEUED:
                        bridge.claim_sync(now=timestamp)
                    bridge.mark_sync_queued(now=timestamp)
                try:
                    queue_bridge_sync(bridge, now=timestamp, persist=False)
                except Exception:
                    with transaction.atomic():
                        reset = model._default_manager.lock_if_supported().filter(pk=pk).first()
                        if reset is not None and reset.sync_queue_token_matches(timestamp):
                            reset.reset_sync_queue(now=timestamp)
                    raise
                enqueued += 1

    return {"enqueued": enqueued, "skipped": skipped}


def _due_or_stale_queue_filter(timestamp: datetime, stale_before: datetime) -> Q:
    """Return rows due for a new queue attempt or stale queued recovery."""

    return Q(next_sync_at__lte=timestamp) | Q(
        sync_stage=Bridge.SyncStage.QUEUED,
        updated_at__lte=stale_before,
    )
