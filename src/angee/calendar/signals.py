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
    """Judge the row a delete starts from, so removing a subscription cascades and editing it does not.

    A delete that starts at a calendar, event or attendee is refused when that row's
    ``delete_blocker`` refuses it (a source's calendar on its own, or a read-only
    calendar's event or attendee outside its source's own work). A cascade from
    removing the source proceeds. A queryset delete judges each row it starts from,
    reading each calendar's writability once (a source's purge reads it once per
    calendar, not per row); a row it only cascades to (an event's attendees) goes
    with the row it started from.
    """

    if isinstance(origin, models.Model):
        root = origin
    elif isinstance(origin, models.QuerySet) and not isinstance(instance, origin.model):
        return
    else:
        root = instance
    if not isinstance(root, _OWNED):
        return
    if isinstance(root, CalendarEvent) and isinstance(origin, models.QuerySet):
        writable = _WRITABLE.setdefault(origin, {})
        if root.calendar_id not in writable:
            writable[root.calendar_id] = root.calendar.takes_write()
        blocker = root.delete_blocker(takes_write=writable[root.calendar_id])
    else:
        blocker = root.delete_blocker()
    if blocker:
        raise ValidationError(blocker)
