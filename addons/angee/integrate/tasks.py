"""Celery task wrappers for the integrate bridge scheduler."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from rebac import system_context

from angee.integrate import scheduler
from angee.integrate.sync_runner import run_bridge_sync_job
from angee.tasks.enqueue import enqueue_task


def queue_bridge_sync(bridge: Any, *, now: datetime | None = None) -> None:
    """Persist queued state and send one bridge sync task."""

    if bridge.pk is None:
        raise ValueError("Cannot queue an unsaved bridge.")
    timestamp = now or timezone.now()
    bridge.sync_stage = bridge.SyncStage.QUEUED
    bridge.sync_error = ""
    bridge.sync_progress = {"stage": bridge.SyncStage.QUEUED, "queued_at": timestamp.isoformat()}
    with system_context(reason="integrate.queue_bridge_sync"), transaction.atomic():
        bridge.save(update_fields=["sync_error", "sync_progress", "sync_stage", "updated_at"])
    enqueue_task(
        "integrate.sync_bridge_now",
        kwargs={
            "model_label": bridge._meta.label_lower,
            "pk": bridge.pk,
            "timestamp": timestamp.isoformat(),
        },
    )


@shared_task(
    name="integrate.sync_bridge_now",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_bridge_now(model_label: str, pk: int, timestamp: str | None = None) -> dict[str, Any]:
    """Run one queued bridge sync task."""

    return run_bridge_sync_job(model_label, pk, timestamp)


@shared_task(
    name="integrate.sync_due_bridges",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def sync_due_bridges(timestamp: int | None = None) -> None:
    """Queue every bridge row whose ``next_sync_at`` is due."""

    del timestamp
    scheduler.enqueue_due_bridges()
