"""Bridge sync queueing use-cases for the integrate addon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from angee.jobs.enqueue import enqueue_task
from django.db import transaction
from django.utils import timezone
from rebac import system_context


def queue_bridge_sync(bridge: Any, *, now: datetime | None = None, persist: bool = True) -> None:
    """Mark and send one bridge sync task."""

    if bridge.pk is None:
        raise ValueError("Cannot queue an unsaved bridge.")
    timestamp = now or timezone.now()
    if persist:
        with system_context(reason="integrate.queue_bridge_sync"), transaction.atomic():
            bridge.mark_sync_queued(now=timestamp)
    enqueue_task(
        "integrate.sync_bridge_now",
        kwargs={
            "model_label": bridge._meta.label_lower,
            "pk": bridge.pk,
            "timestamp": timestamp.isoformat(),
        },
    )
