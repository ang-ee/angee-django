"""Workflow trigger dispatch for event and schedule starts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any, cast

from croniter import croniter
from django.apps import apps
from django.db import OperationalError, ProgrammingError, models, transaction
from django.db.models.signals import post_save
from django.utils import timezone
from rebac import system_context

from angee.workflows.models import TriggerKind

_EVENT_TRIGGER_DISPATCH_UID = "angee-workflows-event-triggers"


def connect_event_trigger_receiver() -> None:
    """Connect the generic event-trigger receiver exactly once."""

    post_save.connect(_on_model_save, dispatch_uid=_EVENT_TRIGGER_DISPATCH_UID)


def run_due_schedule_triggers(*, now: datetime | None = None) -> dict[str, int]:
    """Start enabled schedule triggers due at ``now``."""

    timestamp = now or timezone.now()
    _prime_schedule_triggers(timestamp=timestamp)
    trigger_model = _model("Trigger")
    with system_context(reason="workflows.schedule_triggers.scan"):
        trigger_ids = list(
            trigger_model.objects.filter(
                kind=TriggerKind.SCHEDULE,
                enabled=True,
                next_fire_at__isnull=False,
                next_fire_at__lte=timestamp,
            )
            .order_by("next_fire_at", "pk")
            .values_list("pk", flat=True)
        )

    fired = 0
    skipped = 0
    for trigger_id in trigger_ids:
        claimed = _claim_schedule_trigger(trigger_id, timestamp=timestamp)
        if claimed is None:
            skipped += 1
            continue
        trigger, due_at = claimed
        _enqueue_start(trigger, subject=None, dedup_key=f"schedule:{trigger.pk}:{due_at.isoformat()}")
        fired += 1
    return {"triggers": len(trigger_ids), "fired": fired, "skipped": skipped}


def initial_schedule_fire_at(config: Mapping[str, Any], *, now: datetime) -> datetime | None:
    """Return the first persisted due timestamp for a schedule trigger."""

    interval = _positive_int(config.get("interval_seconds"))
    if interval is not None:
        return now + timedelta(seconds=interval)

    cron = str(config.get("cron", "") or "")
    if not cron:
        return None
    return cast(datetime, croniter(cron, now).get_next(datetime))


def next_schedule_fire_at(config: Mapping[str, Any], *, after: datetime, now: datetime) -> datetime | None:
    """Return the next scheduled occurrence after ``after`` and later than ``now``."""

    interval = _positive_int(config.get("interval_seconds"))
    if interval is not None:
        next_at = after + timedelta(seconds=interval)
        while next_at <= now:
            next_at += timedelta(seconds=interval)
        return next_at

    cron = str(config.get("cron", "") or "")
    if not cron:
        return None
    iterator = croniter(cron, after)
    next_at = cast(datetime, iterator.get_next(datetime))
    while next_at <= now:
        iterator = croniter(cron, next_at)
        next_at = cast(datetime, iterator.get_next(datetime))
    return next_at


def _on_model_save(
    sender: type[models.Model],
    instance: models.Model,
    raw: bool = False,
    **kwargs: Any,
) -> None:
    """Start matching event triggers after a model row is saved."""

    del kwargs
    if raw or instance.pk is None or _bridge_sync_active():
        return

    model_label = sender._meta.label_lower
    trigger_model = _model("Trigger")
    try:
        triggers = list(
            trigger_model._base_manager.filter(
                kind=TriggerKind.EVENT,
                enabled=True,
                event_model_label=model_label,
            )
            .select_related("workflow")
            .order_by("pk")
        )
    except (ProgrammingError, OperationalError):
        # Saves fire during ``migrate`` while the trigger table/columns are
        # still mid-flight; there is nothing to dispatch until the schema
        # exists, and probing it per save would cost a query on every write.
        return
    for trigger in triggers:
        if not _condition_matches(sender, instance, trigger):
            continue
        claimed = _claim_event_trigger(trigger.pk, timestamp=timezone.now())
        if claimed is not None:
            _enqueue_start(claimed, subject=instance)


def _condition_matches(sender: type[models.Model], instance: models.Model, trigger: Any) -> bool:
    config = _config(trigger)
    condition = config.get("condition", {})
    if not isinstance(condition, Mapping):
        return False
    with system_context(reason="workflows.event_triggers.condition"):
        return sender._default_manager.filter(pk=instance.pk, **dict(condition)).exists()


def _claim_event_trigger(trigger_id: int, *, timestamp: datetime) -> Any | None:
    trigger_model = _model("Trigger")
    with system_context(reason="workflows.event_triggers.claim"), transaction.atomic():
        trigger = (
            trigger_model.objects.select_for_update()
            .select_related("workflow")
            .filter(pk=trigger_id, kind=TriggerKind.EVENT, enabled=True)
            .first()
        )
        if trigger is None or not _rate_limit_allows(trigger, timestamp=timestamp):
            return None
        _record_fire(trigger, timestamp=timestamp)
        return trigger


def _claim_schedule_trigger(trigger_id: int, *, timestamp: datetime) -> tuple[Any, datetime] | None:
    trigger_model = _model("Trigger")
    with system_context(reason="workflows.schedule_triggers.claim"), transaction.atomic():
        trigger = (
            trigger_model.objects.select_for_update()
            .select_related("workflow")
            .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True)
            .first()
        )
        if trigger is None or trigger.next_fire_at is None or trigger.next_fire_at > timestamp:
            return None
        due_at = trigger.next_fire_at
        trigger.next_fire_at = next_schedule_fire_at(_config(trigger), after=due_at, now=timestamp)
        if not _rate_limit_allows(trigger, timestamp=timestamp):
            trigger.save(update_fields={"next_fire_at", "updated_at"})
            return None
        _record_fire(trigger, timestamp=timestamp, extra_update_fields=("next_fire_at",))
        return trigger, due_at


def _prime_schedule_triggers(*, timestamp: datetime) -> int:
    trigger_model = _model("Trigger")
    with system_context(reason="workflows.schedule_triggers.prime"):
        trigger_ids = list(
            trigger_model.objects.filter(
                kind=TriggerKind.SCHEDULE,
                enabled=True,
                next_fire_at__isnull=True,
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    primed = 0
    for trigger_id in trigger_ids:
        with system_context(reason="workflows.schedule_triggers.prime"), transaction.atomic():
            trigger = (
                trigger_model.objects.select_for_update()
                .filter(pk=trigger_id, kind=TriggerKind.SCHEDULE, enabled=True, next_fire_at__isnull=True)
                .first()
            )
            if trigger is None:
                continue
            trigger.next_fire_at = initial_schedule_fire_at(_config(trigger), now=timestamp)
            if trigger.next_fire_at is None:
                continue
            trigger.save(update_fields={"next_fire_at", "updated_at"})
            primed += 1
    return primed


def _record_fire(trigger: Any, *, timestamp: datetime, extra_update_fields: Iterable[str] = ()) -> None:
    window_start = trigger.hourly_window_started_at
    if window_start is None or timestamp - window_start >= timedelta(hours=1):
        trigger.hourly_window_started_at = timestamp
        trigger.hourly_fire_count = 0
    trigger.hourly_fire_count += 1
    trigger.last_fire_at = timestamp
    trigger.save(
        update_fields={
            "last_fire_at",
            "hourly_window_started_at",
            "hourly_fire_count",
            "updated_at",
            *extra_update_fields,
        }
    )


def _rate_limit_allows(trigger: Any, *, timestamp: datetime) -> bool:
    config = _config(trigger)
    cooldown_seconds = _non_negative_int(config.get("cooldown_seconds"))
    if cooldown_seconds and trigger.last_fire_at is not None:
        if trigger.last_fire_at + timedelta(seconds=cooldown_seconds) > timestamp:
            return False

    hourly_cap = _positive_int(config.get("hourly_cap"))
    if hourly_cap is None:
        return True
    window_start = trigger.hourly_window_started_at
    if window_start is None or timestamp - window_start >= timedelta(hours=1):
        return True
    return int(trigger.hourly_fire_count) < hourly_cap


def _enqueue_start(trigger: Any, *, subject: models.Model | None, dedup_key: str | None = None) -> None:
    def start() -> None:
        from angee.workflows import engine

        engine.start(trigger.workflow, subject=subject, actor=None, trigger=trigger, dedup_key=dedup_key)

    transaction.on_commit(start)


def _config(trigger: Any) -> Mapping[str, Any]:
    return trigger.config if isinstance(trigger.config, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    parsed = _non_negative_int(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _non_negative_int(value: Any) -> int | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _bridge_sync_active() -> bool:
    try:
        from angee.integrate.sync import bridge_sync_active
    except ImportError:
        return False
    return bridge_sync_active()


def _model(name: str) -> type[Any]:
    return apps.get_model("workflows", name)


