"""Workflow trigger dispatch for event and schedule starts."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from croniter import CroniterBadCronError
from django.apps import apps
from django.core.exceptions import FieldError
from django.db import OperationalError, ProgrammingError, models, transaction
from django.db.models.signals import post_delete, post_save
from django.utils import timezone
from rebac import system_context

from angee.base.models import instance_from_public_id
from angee.graphql.events import ChangePayload
from angee.graphql.publishing import change_published
from angee.workflows import engine
from angee.workflows.models import TriggerKind

_EVENT_TRIGGER_DISPATCH_UID = "angee-workflows-event-triggers"
_TRIGGER_CACHE_DISPATCH_UID = "angee-workflows-trigger-cache"
_EVENT_TRIGGER_LABEL_TTL_SECONDS = 5.0
_event_trigger_label_cache: tuple[float, frozenset[str]] | None = None
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


def run_due_schedule_triggers(*, now: datetime | None = None) -> dict[str, int]:
    """Start enabled schedule triggers due at ``now``."""

    timestamp = now or timezone.now()
    trigger_model = _model("Trigger")
    trigger_model.objects.prime_due_schedules(timestamp=timestamp)
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
        try:
            claimed = trigger_model.objects.claim_due_schedule(trigger_id, timestamp=timestamp)
        except CroniterBadCronError, ValueError, TypeError:
            logger.exception("Skipping workflow schedule trigger %s after next fire calculation failed.", trigger_id)
            claimed = None
        if claimed is None:
            skipped += 1
            continue
        trigger, due_at = claimed
        _enqueue_start(trigger, subject=None, dedup_key=f"schedule:{trigger.pk}:{due_at.isoformat()}")
        fired += 1
    return {"triggers": len(trigger_ids), "fired": fired, "skipped": skipped}


def _on_change_published(
    sender: type[models.Model],
    payload: ChangePayload,
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
    if model_label not in _enabled_event_model_labels():
        return
    with system_context(reason="workflows.event_triggers.subject"):
        instance = instance_from_public_id(sender, payload.id)
    if instance is None:
        return

    try:
        trigger_model = _model("Trigger")
        triggers = list(
            trigger_model._base_manager.filter(
                kind=TriggerKind.EVENT,
                enabled=True,
                event_model_label=model_label,
            )
            .select_related("workflow")
            .order_by("pk")
        )
    except LookupError:
        return
    except ProgrammingError, OperationalError:
        # Saves fire during ``migrate`` while the trigger table/columns are
        # still mid-flight; there is nothing to dispatch until the schema
        # exists, and probing it per save would cost a query on every write.
        return
    for trigger in triggers:
        try:
            matches = trigger.condition_matches(sender, instance)
        except FieldError, ValueError, TypeError:
            logger.exception("Skipping workflow event trigger %s after condition evaluation failed.", trigger.pk)
            continue
        if not matches:
            continue
        claimed = trigger_model.objects.claim_due_event(trigger.pk, timestamp=timezone.now())
        if claimed is not None:
            _enqueue_start(claimed, subject=instance)


def _enqueue_start(trigger: Any, *, subject: models.Model | None, dedup_key: str | None = None) -> None:
    def start() -> None:
        try:
            engine.start(trigger.workflow, subject=subject, actor=None, trigger=trigger, dedup_key=dedup_key)
        except Exception:
            logger.exception("Workflow trigger %s failed to start workflow.", trigger.pk)

    transaction.on_commit(start)


def _enabled_event_model_labels() -> frozenset[str]:
    """Return enabled event model labels with a short in-process cache."""

    global _event_trigger_label_cache
    now = time.monotonic()
    if _event_trigger_label_cache is not None:
        expires_at, labels = _event_trigger_label_cache
        if expires_at > now:
            return labels
    try:
        trigger_model = _model("Trigger")
        labels = frozenset(
            str(label)
            for label in trigger_model._base_manager.filter(
                kind=TriggerKind.EVENT,
                enabled=True,
            )
            .exclude(event_model_label="")
            .values_list("event_model_label", flat=True)
        )
    except LookupError:
        labels = frozenset()
    except ProgrammingError, OperationalError:
        labels = frozenset()
    _event_trigger_label_cache = (now + _EVENT_TRIGGER_LABEL_TTL_SECONDS, labels)
    return labels


def _invalidate_trigger_cache_on_change(
    **kwargs: Any,
) -> None:
    """Invalidate enabled event-label cache when a Trigger row changes."""

    del kwargs
    _clear_event_trigger_label_cache()


def _clear_event_trigger_label_cache() -> None:
    """Clear the process-local enabled event-label cache."""

    global _event_trigger_label_cache
    _event_trigger_label_cache = None


def _model(name: str) -> type[Any]:
    return apps.get_model("workflows", name)
