"""Calendar deletion refusals on instance, queryset and cascade deletion paths."""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models
from angee.calendar.models import Calendar, CalendarEvent, EventAttendee

_OWNED = (Calendar, CalendarEvent, EventAttendee)

_WRITABLE: WeakKeyDictionary[models.QuerySet[Any], dict[Any, bool]] = WeakKeyDictionary()
"""One queryset delete's ``Calendar.takes_write`` answer per calendar, kept only while that queryset lives."""


def connect() -> None:
    """Wire the refusal to concrete calendars, events and attendees."""

    connect_for_models(
        pre_delete,
        refuse_source_owned_delete,
        applies=lambda model: issubclass(model, _OWNED),
        dispatch_uid="angee.calendar.delete_blocker",
    )


def refuse_source_owned_delete(sender: Any, instance: Any, *, origin: Any, **kwargs: Any) -> None:
    """Refuse a calendar, event or attendee delete its ``delete_blocker`` refuses, for where the row sits in it.

    A row the delete started from (an instance delete's own row, a queryset
    delete's rows, every row of a delete without an origin) is judged as the
    start; any other is ``cascaded``. So removing a subscription cascades to its
    calendars and events, editing them does not, and a cascade to a published
    calendar is refused (``Calendar.delete_blocker``,
    ``CalendarEvent.delete_blocker``). A queryset delete reads each calendar's
    writability once for the events it starts from (a source's purge reads it
    once per calendar, not per row).
    """

    if isinstance(origin, models.QuerySet):
        started = isinstance(instance, origin.model)
    else:
        # A delete without an origin judges every row as its own start.
        started = origin is None or origin is instance
    if started and isinstance(instance, CalendarEvent) and isinstance(origin, models.QuerySet):
        writable = _WRITABLE.setdefault(origin, {})
        if instance.calendar_id not in writable:
            writable[instance.calendar_id] = instance.calendar.takes_write()
        blocker = instance.delete_blocker(takes_write=writable[instance.calendar_id])
    else:
        blocker = instance.delete_blocker(cascaded=not started)
    if blocker:
        raise ValidationError(blocker)
