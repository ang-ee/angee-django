"""Execution helpers for queued bridge sync jobs."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rebac import system_context

from angee.integrate.locks import bridge_advisory_lock
from angee.integrate.models import Bridge, BridgeSyncOccurrence


def run_bridge_sync_job(
    model_label: str,
    pk: int,
    timestamp: str | datetime | None = None,
    *,
    generation: str | None = None,
    occurrence: dict[str, str] | None = None,
    require_queue_token: bool = False,
) -> dict[str, Any]:
    """Run one concrete bridge sync job through the shared lock/lifecycle path."""

    now = _parse_timestamp(timestamp)
    issued = _parse_occurrence(occurrence)
    model = _bridge_model(model_label)
    with system_context(reason="integrate.bridge_sync_job"):
        # The first read identifies the stable advisory-lock key only. Every
        # mutable admission fact is re-read after that lock is acquired.
        bridge = model._default_manager.get(pk=pk)
        with bridge_advisory_lock(bridge) as acquired:
            if not acquired:
                # The holder owns this bridge; this run declines. Clear our own queue
                # claim so the stale-queue recovery stops re-queuing a row nobody will
                # ever pick up — a live session holds the lock for its whole life.
                if issued is not None or not require_queue_token:
                    bridge.release_sync_queue(now=now, generation=generation, occurrence=issued)
                return {"ok": True, "items": 0, "skipped": True}
            bridge = model._default_manager.get(pk=pk)
            if require_queue_token and (
                issued is None or not bridge.sync_queue_token_matches(now, generation, issued)
            ):
                if issued is not None:
                    bridge.release_sync_queue(now=now, generation=generation, occurrence=issued)
                return {"ok": True, "items": 0, "skipped": True, "stale": True}
            try:
                if issued is not None:
                    bridge.validate_queued_sync_admission(issued)
                else:
                    bridge.validate_sync_admission(allow_paused=True)
            except ValidationError:
                if issued is not None:
                    bridge.release_sync_queue(now=now, generation=generation, occurrence=issued)
                return {"ok": True, "items": 0, "skipped": True, "ineligible": True}
            receipt = bridge.dispatch_sync(now=now)
    return {"ok": True, **receipt.as_payload(), "skipped": False}


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


def _parse_occurrence(value: dict[str, str] | None) -> BridgeSyncOccurrence | None:
    """Return one explicit queue occurrence, rejecting implicit reconstruction."""

    if value is None:
        return None
    try:
        return BridgeSyncOccurrence.from_payload(value)
    except ValueError:
        return None


__all__ = ["run_bridge_sync_job"]
