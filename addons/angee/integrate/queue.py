"""Bridge sync queueing use-cases for the integrate addon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.jobs.enqueue import enqueue_task


def queue_bridge_sync(
    bridge: Any, *, now: datetime | None = None, persist: bool = True, using: str | None = None
) -> None:
    """Mark and send one bridge sync task."""

    if bridge.pk is None:
        raise ValueError("Cannot queue an unsaved bridge.")
    using = get_write_alias(type(bridge), using=using, instance=bridge)
    bridge._state.db = using
    timestamp = now or timezone.now()
    if persist:
        with system_context(reason="integrate.queue_bridge_sync"), transaction.atomic(using=using):
            bridge.mark_sync_queued(now=timestamp)
    enqueue_task(
        "integrate.sync_bridge_now",
        kwargs={
            "model_label": bridge._meta.label_lower,
            "pk": bridge.pk,
            "timestamp": timestamp.isoformat(),
            "using": using,
        },
    )
