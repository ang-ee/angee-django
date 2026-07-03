"""Pure scheduler logic for due integration bridges."""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import Bridge
from angee.integrate.registry import bridge_models


def run_due_bridges(*, now: datetime | None = None) -> dict[str, int]:
    """Run every bridge row due at ``now`` and return scheduler counters.

    Each due row is re-read and claimed under a row lock (``Bridge.claim_sync``
    pushes its ``next_sync_at`` one interval out) before its sync runs, so
    overlapping scans — a backfill outliving the tick cadence, or two workers —
    skip an in-flight bridge instead of double-syncing it. The eager
    ``syncIntegration`` mutation bypasses the schedule by design; ingest
    idempotency keeps that rarer race convergent.
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
                ran += 1
                try:
                    bridge.run_sync(now=timestamp)
                except Exception:  # noqa: BLE001 — run_sync recorded the bridge failure as telemetry.
                    errors += 1

    return {"ran": ran, "errors": errors}
