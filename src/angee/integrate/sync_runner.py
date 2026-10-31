"""Execution helpers for queued bridge sync jobs."""

from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
from typing import Any

from django.apps import apps
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rebac import database_context, system_context
from rebac.database import write_alias

from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import Bridge
from angee.integrate.sync import SyncDispatch


def run_bridge_sync_job(
    model_label: str,
    pk: int,
    timestamp: str | datetime | None = None,
    *,
    require_queue_token: bool = False,
    using: str | None = None,
) -> dict[str, Any]:
    """Run the producer alias or native model write router through the shared lock/lifecycle path."""

    now = _parse_timestamp(timestamp)
    model = _bridge_model(model_label)
    using = write_alias(model, using=using)
    with database_context(using), system_context(reason="integrate.bridge_sync_job"), ExitStack() as locks:
        with transaction.atomic(using=using):
            bridge = model._default_manager.db_manager(using).lock_if_supported().get(pk=pk)
            if not bridge.discovery_ready:
                return {"ok": False, "items": 0, "skipped": True, "reason": "connection-not-ready"}
            if require_queue_token and not bridge.sync_queue_token_matches(now):
                return {"ok": True, "items": 0, "skipped": True, "stale": True}
            acquired = locks.enter_context(bridge_advisory_lock(bridge))
            if not acquired:
                # The holder owns this bridge; this run declines. Clear our own queue
                # claim so the stale-queue recovery stops re-queuing a row nobody will
                # ever pick up — a live session holds the lock for its whole life.
                bridge.release_sync_queue(now=now)
                return {"ok": True, "items": 0, "skipped": True}
            if require_queue_token and not bridge.sync_is_dispatched:
                # Consume the token before another delivery can decline and clear it.
                bridge.mark_sync_started(now=now)
        items = bridge.run_sync(now=now)
    if items is SyncDispatch.DISPATCHED:
        return {"ok": True, "items": 0, "skipped": False, "dispatched": True}
    return {"ok": True, "items": items, "skipped": False}


def _bridge_model(model_label: str) -> type[Bridge]:
    """Resolve and validate one concrete bridge model label."""

    try:
        app_label, model_name = model_label.split(".", 1)
    except ValueError as error:
        raise ValueError(f"Invalid bridge model label: {model_label}") from error
    model = apps.get_model(app_label, model_name)
    if not issubclass(model, Bridge):
        raise ValueError(f"Model is not a bridge: {model_label}")
    return model


def _parse_timestamp(value: str | datetime | None) -> datetime:
    """Return an aware timestamp for one queued sync job."""

    if value is None:
        return timezone.now()
    if isinstance(value, datetime):
        timestamp = value
    else:
        parsed = parse_datetime(value)
        if parsed is None:
            raise ValueError(f"Invalid bridge sync timestamp: {value}")
        timestamp = parsed
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


__all__ = ["run_bridge_sync_job"]
