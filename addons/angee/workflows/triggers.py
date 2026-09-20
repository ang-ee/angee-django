"""Workflow trigger dispatch for event and schedule starts."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from croniter import CroniterBadCronError
from django.apps import apps
from django.db import OperationalError, ProgrammingError, models
from django.db.models.signals import post_delete, post_save
from django.utils import timezone
from rebac import system_context

from angee.base.db import get_write_alias
from angee.base.identity import instance_from_public_id
from angee.base.scoping import system_queryset
from angee.graphql.events import ChangePayload
from angee.graphql.publishing import change_published
from angee.workflows.models import TriggerKind
from angee.workflows.trigger_declarations import EventSource

_EVENT_TRIGGER_DISPATCH_UID = "angee-workflows-event-triggers"
_TRIGGER_CACHE_DISPATCH_UID = "angee-workflows-trigger-cache"
_EVENT_TRIGGER_LABEL_TTL_SECONDS = 5.0
_event_trigger_label_cache: dict[str, tuple[float, frozenset[str]]] = {}
logger = logging.getLogger(__name__)


def connect_event_trigger_receiver() -> None:
    """Connect the generic event-trigger receiver exactly once."""

    _clear_event_trigger_label_cache()
    change_published.connect(
        _on_change_published,
        dispatch_uid=_EVENT_TRIGGER_DISPATCH_UID,
    )
    try:
        trigger_model = _model("Trigger")
    except LookupError:
        return
    post_save.connect(
        _invalidate_trigger_cache_on_change,
        sender=trigger_model,
        dispatch_uid=f"{_TRIGGER_CACHE_DISPATCH_UID}:save",
    )
    post_delete.connect(
        _invalidate_trigger_cache_on_change,
        sender=trigger_model,
        dispatch_uid=f"{_TRIGGER_CACHE_DISPATCH_UID}:delete",
    )


def run_due_schedule_triggers(*, now: datetime | None = None, using: str | None = None) -> dict[str, int]:
    """Start enabled schedule triggers due at ``now``."""

    timestamp = now or timezone.now()
    trigger_model = _model("Trigger")
    alias = get_write_alias(trigger_model, using=using)
    trigger_model.objects.db_manager(alias).prime_due_schedules(timestamp=timestamp)
    with system_context(reason="workflows.schedule_triggers.scan"):
        trigger_ids = list(
            trigger_model.objects.using(alias).filter(
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
        try:
            claimed = trigger_model.objects.db_manager(alias).start_due_schedule(trigger_id, timestamp=timestamp)
        except (CroniterBadCronError, ValueError, TypeError):
            logger.exception("Skipping workflow schedule trigger %s after next fire calculation failed.", trigger_id)
            claimed = None
        except Exception:
            logger.exception("Workflow schedule trigger %s failed to start workflow.", trigger_id)
            claimed = None
        if claimed is None:
            skipped += 1
            continue
        _run, _due_at = claimed
        fired += 1
    return {"triggers": len(trigger_ids), "fired": fired, "skipped": skipped}


def _on_change_published(
    sender: type[models.Model],
    payload: ChangePayload,
    *,
    using: str,
    **kwargs: Any,
) -> None:
    """Start matching event triggers after an observable model change is published."""

    del kwargs
    if payload.action == "delete":
        return
    if payload.during_ingestion:
        return

    model_label = payload.model.lower()
    if model_label.startswith("workflows."):
        return
    alias = using
    if model_label not in _enabled_event_model_labels(using=alias):
        return
    with system_context(reason="workflows.event_triggers.subject"):
        instance = instance_from_public_id(sender, payload.id, queryset=system_queryset(sender, using=alias))
    if instance is None:
        return

    try:
        trigger_model = _model("Trigger")
        triggers = list(
            trigger_model._base_manager.using(alias).filter(
                kind=TriggerKind.EVENT,
                enabled=True,
                event_model_label=model_label,
            )
            .select_related("workflow")
            .order_by("pk")
        )
    except LookupError:
        return
    except (ProgrammingError, OperationalError):
        # Saves fire during ``migrate`` while the trigger table/columns are
        # still mid-flight; there is nothing to dispatch until the schema
        # exists, and probing it per save would cost a query on every write.
        return
    for trigger in triggers:
        try:
            if trigger.validated_config().source != EventSource.CHANGE_PUBLISHED:
                continue
            trigger_model.objects.db_manager(alias).start_event(
                trigger.pk,
                subject=instance,
                occurrence_id=payload.occurrence_id,
                timestamp=timezone.now(),
                source=EventSource.CHANGE_PUBLISHED,
            )
        except Exception:
            logger.exception("Workflow event trigger %s failed admission.", trigger.pk)


def _enabled_event_model_labels(*, using: str) -> frozenset[str]:
    """Return enabled event model labels with a short in-process cache."""

    alias = using
    now = time.monotonic()
    cached = _event_trigger_label_cache.get(alias)
    if cached is not None:
        expires_at, labels = cached
        if expires_at > now:
            return labels
    try:
        trigger_model = _model("Trigger")
        labels = frozenset(
            str(label)
            for label in trigger_model._base_manager.using(alias).filter(
                kind=TriggerKind.EVENT,
                enabled=True,
            )
            .exclude(event_model_label="")
            .values_list("event_model_label", flat=True)
        )
    except LookupError:
        labels = frozenset()
    except (ProgrammingError, OperationalError):
        labels = frozenset()
    _event_trigger_label_cache[alias] = (now + _EVENT_TRIGGER_LABEL_TTL_SECONDS, labels)
    return labels


def _invalidate_trigger_cache_on_change(
    **kwargs: Any,
) -> None:
    """Invalidate enabled event-label cache when a Trigger row changes."""

    del kwargs
    _clear_event_trigger_label_cache()


def _clear_event_trigger_label_cache() -> None:
    """Clear the process-local enabled event-label cache."""

    _event_trigger_label_cache.clear()


def _model(name: str) -> type[Any]:
    return apps.get_model("workflows", name)
