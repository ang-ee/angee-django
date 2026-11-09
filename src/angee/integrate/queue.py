"""Bridge sync queueing use-cases for the integrate addon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

from angee.jobs.enqueue import enqueue_task


def queue_bridge_sync(bridge: Any, *, now: datetime | None = None, persist: bool = True) -> bool:
    """Send one admitted sync task; return false when an execution already owns it."""

    if bridge.pk is None:
        raise ValueError("Cannot queue an unsaved bridge.")
    timestamp = now or timezone.now()
    if persist and not bridge.mark_sync_queued(now=timestamp):
        return False
    enqueue_task(
        "integrate.sync_bridge_now",
        kwargs={
            "model_label": bridge._meta.label_lower,
            "pk": bridge.pk,
            "timestamp": timestamp.isoformat(),
        },
    )
    return True
