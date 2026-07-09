"""Pure scheduler logic for due integration bridges."""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import Bridge
from angee.integrate.registry import bridge_models
from angee.integrate.sync_runner import run_bridge_sync_job


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

    from angee.integrate.tasks import queue_bridge_sync

    timestamp = now or timezone.now()
    enqueued = 0
    skipped = 0

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
                        skipped += 1
                        continue
                    bridge.claim_sync(now=timestamp)
                queue_bridge_sync(bridge, now=timestamp)
                enqueued += 1

    return {"enqueued": enqueued, "skipped": skipped}
