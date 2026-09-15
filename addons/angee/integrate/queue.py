"""Bridge sync queueing use-cases for the integrate addon."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.db import DEFAULT_DB_ALIAS, transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate.models import BridgeSyncOccurrence
from angee.jobs.enqueue import enqueue_task


def queue_bridge_sync(
    bridge: Any,
    *,
    now: datetime | None = None,
    persist: bool = True,
    occurrence: BridgeSyncOccurrence | None = None,
) -> None:
    """Admit, durably mark, and enqueue one bridge sync task."""

    if bridge.pk is None:
        raise ValueError("Cannot queue an unsaved bridge.")
    if bridge._state.db not in (None, DEFAULT_DB_ALIAS):
        raise ValueError("Bridge sync queueing requires the default database.")
    alias = DEFAULT_DB_ALIAS
    timestamp = now or timezone.now()
    issued = occurrence or BridgeSyncOccurrence(
        kind="manual",
        key=f"manual:{timestamp.isoformat()}",
        occurred_at=timestamp,
        window_key=timestamp.isoformat(),
    )
    if persist:
        with system_context(reason="integrate.queue_bridge_sync"), transaction.atomic(
            using=alias
        ):
            row = (
                type(bridge)
                .objects.sudo(reason="integrate.queue_bridge_sync.row")
                .using(alias)
                .lock_if_supported()
                .get(pk=bridge.pk)
            )
            row.mark_sync_queued(now=timestamp, occurrence=issued)
            generation = row.sync_admission_generation()
        bridge.sync_stage = row.sync_stage
        bridge.sync_error = row.sync_error
        bridge.sync_progress = row.sync_progress
    else:
        generation = bridge.sync_admission_generation()
    task_kwargs = {
        "model_label": bridge._meta.label_lower,
        "pk": bridge.pk,
        "timestamp": timestamp.isoformat(),
        "generation": generation,
        "occurrence": issued.canonical(),
    }
    transaction.on_commit(
        lambda: enqueue_task("integrate.sync_bridge_now", kwargs=task_kwargs),
        using=alias,
    )
