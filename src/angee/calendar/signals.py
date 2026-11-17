"""Calendar deletion refusals on instance, queryset and cascade deletion paths."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models
from angee.calendar.models import Calendar, CalendarEvent, EventAttendee

_OWNED = (Calendar, CalendarEvent, EventAttendee)


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
    calendar's event or attendee outside its source's sync). A cascade from removing
    the source proceeds; a queryset delete judges each row it collects.
    """

    root = origin if isinstance(origin, models.Model) else instance
    if isinstance(root, _OWNED) and (blocker := root.delete_blocker()):
        raise ValidationError(blocker)
