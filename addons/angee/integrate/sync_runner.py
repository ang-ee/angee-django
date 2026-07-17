"""Execution helpers for queued bridge sync jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rebac import system_context

from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import Bridge


def run_bridge_sync_job(
    model_label: str,
    pk: int,
    timestamp: str | datetime | None = None,
    *,
    require_queue_token: bool = False,
) -> dict[str, Any]:
    """Run one concrete bridge sync job through the shared lock/lifecycle path."""

    now = _parse_timestamp(timestamp)
    model = _bridge_model(model_label)
    with system_context(reason="integrate.bridge_sync_job"):
        bridge = model._default_manager.get(pk=pk)
        if require_queue_token and not bridge.sync_queue_token_matches(now):
            return {"ok": True, "items": 0, "skipped": True, "stale": True}
        with bridge_advisory_lock(bridge) as acquired:
            if not acquired:
                # The holder owns this bridge; this run declines. Clear our own queue
                # claim so the stale-queue recovery stops re-queuing a row nobody will
                # ever pick up — a live session holds the lock for its whole life.
                bridge.release_sync_queue(now=now)
                return {"ok": True, "items": 0, "skipped": True}
            items = bridge.run_sync(now=now)
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
        timestamp = parse_datetime(value)
        if timestamp is None:
            raise ValueError(f"Invalid bridge sync timestamp: {value}")
    if timezone.is_naive(timestamp):
        timestamp = timezone.make_aware(timestamp, timezone.get_current_timezone())
    return timestamp


__all__ = ["run_bridge_sync_job"]
