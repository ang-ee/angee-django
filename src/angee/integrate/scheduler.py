"""Pure scheduler logic for due integration bridges."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from rebac import system_context

from angee.integrate.registry import bridge_models


def run_due_bridges(*, now: datetime | None = None) -> dict[str, int]:
    """Run every bridge row due at ``now`` and return scheduler counters."""

    timestamp = now or timezone.now()
    ran = 0
    errors = 0

    with system_context(reason="integrate.scheduler"):
        for model in bridge_models():
            due_bridges = model._default_manager.filter(next_sync_at__lte=timestamp).order_by("pk")
            for bridge in due_bridges:
                ran += 1
                bridge.mark_sync_started(now=timestamp)
                try:
                    bridge.record_sync(bridge.sync(), now=timestamp)
                except Exception as error:
                    bridge.record_sync_error(error, now=timestamp)
                    errors += 1

    return {"ran": ran, "errors": errors}
